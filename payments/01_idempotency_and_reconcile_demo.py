"""
Самостоятельный воспроизводимый пример (обезличенная демонстрация механизма):
Идемпотентный приём платежей при таймаутах шлюза, сбоях воркера и гонках позднего ответа.

Проверяемые инварианты (запуск: `python3 01_idempotency_and_reconcile_demo.py`):
1. Строгая валидация входа: целые положительные копейки в допустимом диапазоне 64-bit, белый список ISO-валют,
   непустой order_id, однозначная сериализация области ключа (merchant_id, idempotency_key) без коллизий разделителя.
2. Повтор с тем же (merchant_id, idempotency_key) -> 200 Replay без повторного обращения к провайдеру.
3. Подмена параметров под тем же ключом -> 409 Conflict.
4. Таймаут шлюза -> 202 Accepted (pending_reconcile) и дотягивание статуса read-only запросом без нового charge.
5. Сбой воркера сразу после списания до записи ответа и гонка позднего ответа после перевода в manual_review:
   событие outbox создаётся ровно один раз и только при фактическом переходе БД в статус succeeded.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import unittest
from dataclasses import dataclass
from typing import Any

SUPPORTED_CURRENCIES = frozenset({"RUB", "USD", "EUR", "KZT"})
MAX_AMOUNT_CENTS = 10_000_000_000  # 100 млн руб. в копейках (защита от переполнения INTEGER)


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            merchant_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            merchant_ref TEXT NOT NULL UNIQUE,
            payload_hash TEXT NOT NULL,
            order_id TEXT NOT NULL,
            amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
            currency TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('reserved', 'succeeded', 'pending_reconcile', 'manual_review')
            ),
            provider_tx_id TEXT,
            lease_expires_at INTEGER NOT NULL,
            UNIQUE (merchant_id, idempotency_key)
        );
        CREATE TABLE outbox_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aggregate_key TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        """
    )
    return conn


def make_merchant_ref(merchant_id: str, idempotency_key: str) -> str:
    # Однозначная JSON-сериализация массива исключает коллизию ('a:b', 'c') и ('a', 'b:c')
    scoped = json.dumps([merchant_id, idempotency_key], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(scoped.encode("utf-8")).hexdigest()[:32]


def validate_and_hash(
    merchant_id: Any, idempotency_key: Any, order_id: Any, amount_cents: Any, currency: Any
) -> tuple[str, str, str, int, str, str]:
    if not isinstance(merchant_id, str) or not merchant_id.strip():
        raise ValueError("merchant_id must be a non-empty string")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ValueError("idempotency_key must be a non-empty string")
    if not isinstance(order_id, str) or not order_id.strip():
        raise ValueError("order_id must be a non-empty string")
    if (
        isinstance(amount_cents, bool)
        or not isinstance(amount_cents, int)
        or amount_cents <= 0
        or amount_cents > MAX_AMOUNT_CENTS
    ):
        raise ValueError("amount_cents must be an integer between 1 and 10_000_000_000")
    if not isinstance(currency, str) or not currency.isascii():
        raise ValueError("currency must be an ASCII ISO code")
    norm_curr = currency.strip().upper()
    if norm_curr not in SUPPORTED_CURRENCIES:
        raise ValueError(f"unsupported currency: {norm_curr}")

    norm_merchant = merchant_id.strip()
    norm_key = idempotency_key.strip()
    norm_order = order_id.strip()
    canonical = json.dumps(
        {"order_id": norm_order, "amount_cents": amount_cents, "currency": norm_curr},
        sort_keys=True,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return norm_merchant, norm_key, norm_order, amount_cents, norm_curr, digest


class MockProviderGateway:
    """Имитатор внешнего платёжного провайдера с поддержкой поиска по merchant_ref."""

    def __init__(self) -> None:
        self.ledger: dict[str, dict[str, Any]] = {}
        self.charge_calls = 0
        self.fail_next_with_timeout = False
        self.on_before_charge_return: Any = None

    def charge(self, merchant_ref: str, amount_cents: int, currency: str) -> dict[str, Any]:
        self.charge_calls += 1
        if self.on_before_charge_return is not None:
            hook, self.on_before_charge_return = self.on_before_charge_return, None
            hook()
        tx = self.ledger.setdefault(
            merchant_ref,
            {
                "provider_tx_id": f"prov_{len(self.ledger) + 1:04d}",
                "status": "captured",
                "amount_cents": amount_cents,
                "currency": currency,
            },
        )
        if self.fail_next_with_timeout:
            self.fail_next_with_timeout = False
            raise TimeoutError("ReadTimeout after provider accepted request")
        return tx

    def get_status_by_merchant_ref(self, merchant_ref: str) -> dict[str, Any] | None:
        """Read-only запрос статуса у провайдера: никогда не создаёт новое списание."""
        return self.ledger.get(merchant_ref)


@dataclass
class HttpResponse:
    status_code: int
    body: dict[str, Any]


def create_payment(
    conn: sqlite3.Connection,
    gateway: MockProviderGateway,
    *,
    merchant_id: Any,
    idempotency_key: Any,
    order_id: Any,
    amount_cents: Any,
    currency: Any = "RUB",
    now_ts: int | None = None,
    lease_ttl_sec: int = 30,
    simulate_crash_after_charge: bool = False,
) -> HttpResponse:
    now = int(time.time()) if now_ts is None else now_ts
    try:
        norm_m, norm_k, norm_order, norm_amount, norm_curr, req_hash = validate_and_hash(
            merchant_id, idempotency_key, order_id, amount_cents, currency
        )
    except ValueError as exc:
        return HttpResponse(422, {"error": "validation_error", "detail": str(exc)})

    merchant_ref = make_merchant_ref(norm_m, norm_k)

    try:
        with conn:
            conn.execute(
                """
                INSERT INTO payments (
                    merchant_id, idempotency_key, merchant_ref, payload_hash,
                    order_id, amount_cents, currency, status, lease_expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'reserved', ?)
                """,
                (
                    norm_m,
                    norm_k,
                    merchant_ref,
                    req_hash,
                    norm_order,
                    norm_amount,
                    norm_curr,
                    now + lease_ttl_sec,
                ),
            )
    except sqlite3.IntegrityError:
        row = conn.execute(
            "SELECT * FROM payments WHERE merchant_id = ? AND idempotency_key = ?",
            (norm_m, norm_k),
        ).fetchone()
        if row is None:
            raise
        if row["payload_hash"] != req_hash:
            return HttpResponse(
                409,
                {
                    "error": "idempotency_payload_mismatch",
                    "detail": "Same Idempotency-Key used with different payment parameters",
                },
            )
        return HttpResponse(
            200,
            {
                "idempotent_replay": True,
                "status": row["status"],
                "merchant_ref": row["merchant_ref"],
                "provider_tx_id": row["provider_tx_id"],
            },
        )

    try:
        result = gateway.charge(merchant_ref, norm_amount, norm_curr)
        if simulate_crash_after_charge:
            raise RuntimeError("Worker crashed after gateway charge before DB update")

        with conn:
            # Переход допустим из reserved, pending_reconcile и manual_review, если пришёл достоверный ответ capture
            cur = conn.execute(
                """
                UPDATE payments
                SET status = 'succeeded', provider_tx_id = ?
                WHERE merchant_id = ? AND idempotency_key = ?
                  AND status IN ('reserved', 'pending_reconcile', 'manual_review')
                """,
                (result["provider_tx_id"], norm_m, norm_k),
            )
            if cur.rowcount == 1:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO outbox_events (aggregate_key, event_type, payload_json)
                    VALUES (?, 'payment.succeeded', ?)
                    """,
                    (
                        merchant_ref,
                        json.dumps({"order_id": norm_order, "provider_tx_id": result["provider_tx_id"]}),
                    ),
                )
        row_after = conn.execute(
            "SELECT status, provider_tx_id FROM payments WHERE merchant_id = ? AND idempotency_key = ?",
            (norm_m, norm_k),
        ).fetchone()
        assert row_after is not None
        code = 201 if cur.rowcount == 1 else 200
        return HttpResponse(
            code,
            {
                "status": row_after["status"],
                "merchant_ref": merchant_ref,
                "provider_tx_id": row_after["provider_tx_id"],
            },
        )
    except TimeoutError:
        with conn:
            conn.execute(
                """
                UPDATE payments
                SET status = 'pending_reconcile'
                WHERE merchant_id = ? AND idempotency_key = ? AND status = 'reserved'
                """,
                (norm_m, norm_k),
            )
        row_to = conn.execute(
            "SELECT status FROM payments WHERE merchant_id = ? AND idempotency_key = ?",
            (norm_m, norm_k),
        ).fetchone()
        assert row_to is not None
        return HttpResponse(
            202,
            {
                "status": row_to["status"],
                "merchant_ref": merchant_ref,
                "retry_safe": True,
            },
        )


def reconcile_worker(
    conn: sqlite3.Connection,
    gateway: MockProviderGateway,
    *,
    now_ts: int,
) -> dict[str, int]:
    """
    Внешний запрос к провайдеру строго read-only (`get_status_by_merchant_ref`), локально обновляет БД:
    - 'pending_reconcile' сверяется сразу;
    - 'reserved' сверяется только после истечения lease_expires_at.
    При отсутствии операции у провайдера ставит 'manual_review' для эскалации (не закрывает как failed).
    """
    rows = conn.execute(
        """
        SELECT * FROM payments
        WHERE status = 'pending_reconcile'
           OR (status = 'reserved' AND lease_expires_at <= ?)
        """,
        (now_ts,),
    ).fetchall()

    stats = {"succeeded": 0, "manual_review": 0}
    for row in rows:
        remote = gateway.get_status_by_merchant_ref(row["merchant_ref"])
        if remote and remote["status"] == "captured":
            with conn:
                cur = conn.execute(
                    """
                    UPDATE payments
                    SET status = 'succeeded', provider_tx_id = ?
                    WHERE id = ? AND status IN ('pending_reconcile', 'reserved', 'manual_review')
                    """,
                    (remote["provider_tx_id"], row["id"]),
                )
                if cur.rowcount == 1:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO outbox_events (aggregate_key, event_type, payload_json)
                        VALUES (?, 'payment.succeeded', ?)
                        """,
                        (
                            row["merchant_ref"],
                            json.dumps(
                                {
                                    "order_id": row["order_id"],
                                    "provider_tx_id": remote["provider_tx_id"],
                                }
                            ),
                        ),
                    )
                    stats["succeeded"] += 1
        elif remote is None:
            with conn:
                cur = conn.execute(
                    """
                    UPDATE payments
                    SET status = 'manual_review'
                    WHERE id = ? AND status IN ('pending_reconcile', 'reserved')
                    """,
                    (row["id"],),
                )
                if cur.rowcount == 1:
                    stats["manual_review"] += 1
    return stats


def reconcile_manual_review_by_ref(
    conn: sqlite3.Connection,
    gateway: MockProviderGateway,
    merchant_ref: str,
) -> dict[str, Any]:
    """
    Адресная операторская read-only сверка одной записи 'manual_review' после получения ответа от провайдера
    (соответствует шагу 3 Runbook L2/L3, без нового списания charge).
    """
    row = conn.execute(
        "SELECT * FROM payments WHERE merchant_ref = ?",
        (merchant_ref,),
    ).fetchone()
    if row is None:
        return {"status": "not_found"}
    remote = gateway.get_status_by_merchant_ref(merchant_ref)
    if remote and remote["status"] == "captured":
        with conn:
            cur = conn.execute(
                """
                UPDATE payments
                SET status = 'succeeded', provider_tx_id = ?
                WHERE id = ? AND status IN ('manual_review', 'pending_reconcile', 'reserved')
                """,
                (remote["provider_tx_id"], row["id"]),
            )
            if cur.rowcount == 1:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO outbox_events (aggregate_key, event_type, payload_json)
                    VALUES (?, 'payment.succeeded', ?)
                    """,
                    (
                        row["merchant_ref"],
                        json.dumps({"order_id": row["order_id"], "provider_tx_id": remote["provider_tx_id"]}),
                    ),
                )
        return {"status": "succeeded", "provider_tx_id": remote["provider_tx_id"]}
    return {"status": row["status"], "provider_status": None if remote is None else remote["status"]}


class IdempotencyAndReconcileTests(unittest.TestCase):
    def test_happy_path_201_and_replay_200(self) -> None:
        conn = init_db()
        gw = MockProviderGateway()
        r1 = create_payment(conn, gw, merchant_id="m1", idempotency_key="k1", order_id="ord-1", amount_cents=150000)
        self.assertEqual(r1.status_code, 201)
        r2 = create_payment(conn, gw, merchant_id="m1", idempotency_key="k1", order_id="ord-1", amount_cents=150000)
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.body["idempotent_replay"])
        self.assertEqual(gw.charge_calls, 1)

    def test_validation_rejects_bad_amounts_currencies_and_overflow(self) -> None:
        conn = init_db()
        gw = MockProviderGateway()
        for bad_amount in (-1, 0, "450000", True, 2**63):
            resp = create_payment(
                conn, gw, merchant_id="m1", idempotency_key="k-bad", order_id="ord-1", amount_cents=bad_amount
            )
            self.assertEqual(resp.status_code, 422)
        for bad_curr in ("ZZZ", "РУБ", "RU", ""):
            resp = create_payment(
                conn,
                gw,
                merchant_id="m1",
                idempotency_key="k-curr",
                order_id="ord-1",
                amount_cents=1000,
                currency=bad_curr,
            )
            self.assertEqual(resp.status_code, 422)
        self.assertEqual(gw.charge_calls, 0)

    def test_scope_serialization_prevents_colon_collision(self) -> None:
        conn = init_db()
        gw = MockProviderGateway()
        r_a = create_payment(
            conn, gw, merchant_id="foo:bar", idempotency_key="baz", order_id="ord-a", amount_cents=1000
        )
        r_b = create_payment(
            conn, gw, merchant_id="foo", idempotency_key="bar:baz", order_id="ord-b", amount_cents=2000
        )
        self.assertEqual(r_a.status_code, 201)
        self.assertEqual(r_b.status_code, 201)
        self.assertNotEqual(r_a.body["merchant_ref"], r_b.body["merchant_ref"])

    def test_timeout_202_conflict_409_and_reconcile(self) -> None:
        conn = init_db()
        gw = MockProviderGateway()
        gw.fail_next_with_timeout = True

        r1 = create_payment(
            conn, gw, merchant_id="m1", idempotency_key="k-to", order_id="ord-10", amount_cents=450000, now_ts=1000
        )
        self.assertEqual(r1.status_code, 202)
        r_conflict = create_payment(
            conn, gw, merchant_id="m1", idempotency_key="k-to", order_id="ord-10", amount_cents=990000, now_ts=1001
        )
        self.assertEqual(r_conflict.status_code, 409)

        stats = reconcile_worker(conn, gw, now_ts=1005)
        self.assertEqual(stats["succeeded"], 1)
        self.assertEqual(gw.charge_calls, 1)

    def test_crash_after_charge_and_late_capture_after_manual_review_stay_consistent(self) -> None:
        conn = init_db()
        gw = MockProviderGateway()
        with self.assertRaises(RuntimeError):
            create_payment(
                conn,
                gw,
                merchant_id="m1",
                idempotency_key="k-crash",
                order_id="ord-77",
                amount_cents=320000,
                now_ts=2000,
                lease_ttl_sec=30,
                simulate_crash_after_charge=True,
            )
        self.assertEqual(reconcile_worker(conn, gw, now_ts=2010)["succeeded"], 0)
        self.assertEqual(reconcile_worker(conn, gw, now_ts=2031)["succeeded"], 1)
        self.assertEqual(gw.charge_calls, 1)

        # Сценарий гонки: пока шёл медленный charge, lease истёк и воркер увидел not_found -> manual_review,
        # после чего провайдер завершил capture и вернул ответ воркеру запроса: БД и outbox синхронны.
        def expire_to_manual_review() -> None:
            reconcile_worker(conn, gw, now_ts=3050)

        gw.on_before_charge_return = expire_to_manual_review
        late = create_payment(
            conn,
            gw,
            merchant_id="m1",
            idempotency_key="k-slow",
            order_id="ord-88",
            amount_cents=210000,
            now_ts=3000,
            lease_ttl_sec=30,
        )
        self.assertEqual(late.status_code, 201)
        row = conn.execute("SELECT status, provider_tx_id FROM payments WHERE idempotency_key = 'k-slow'").fetchone()
        self.assertEqual(row["status"], "succeeded")
        self.assertIsNotNone(row["provider_tx_id"])
        outbox_cnt = conn.execute(
            "SELECT COUNT(*) FROM outbox_events WHERE aggregate_key = ?", (late.body["merchant_ref"],)
        ).fetchone()[0]
        self.assertEqual(outbox_cnt, 1)

    def test_operator_manual_review_reconcile_recovers_late_provider_visibility(self) -> None:
        conn = init_db()
        gw = MockProviderGateway()
        gw.fail_next_with_timeout = True
        r1 = create_payment(
            conn, gw, merchant_id="m1", idempotency_key="k-late-vis", order_id="ord-99", amount_cents=500000, now_ts=4000
        )
        self.assertEqual(r1.status_code, 202)
        mref = r1.body["merchant_ref"]
        # Провайдер временно ещё не отразил операцию в read-only реестре -> manual_review
        saved_entry = gw.ledger.pop(mref)
        self.assertEqual(reconcile_worker(conn, gw, now_ts=4010)["manual_review"], 1)
        # Позже провайдер подтвердил операцию в реестре -> оператор выполняет адресную сверку без нового charge
        gw.ledger[mref] = saved_entry
        op_res = reconcile_manual_review_by_ref(conn, gw, mref)
        self.assertEqual(op_res["status"], "succeeded")
        self.assertEqual(gw.charge_calls, 1)
        outbox_cnt = conn.execute("SELECT COUNT(*) FROM outbox_events WHERE aggregate_key = ?", (mref,)).fetchone()[0]
        self.assertEqual(outbox_cnt, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
