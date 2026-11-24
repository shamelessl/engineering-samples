"""
Самостоятельный воспроизводимый автотест (запуск: `python3 04_concurrent_version_conflict_test.py`):
Конкурентное сохранение одной карточки заказа с проверкой ревизии (Optimistic Locking) в SQLite.

Что именно проверяет тест (и где граница примера):
1. Два клиента сначала читают актуальную карточку заказа и получают её текущую `version`.
2. Оба одновременно отправляют изменения через синхронизацию на `threading.Barrier` с таймаутом.
3. Победитель атомарно увеличивает `version` условным `UPDATE ... WHERE id = ? AND version = ?` (код 200).
4. Проигравший получает `409 Conflict` с актуальным серверным состоянием и сохранённым собственным черновиком `client_draft` без затирания текста победителя (слияние полей в UI остаётся задачей фронтенда).
5. Обращение к несуществующему заказу возвращает `404 Not Found` (а не исключение или ложный `409`).
"""
from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any


def setup_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(
            """
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                comment TEXT NOT NULL,
                version INTEGER NOT NULL
            )
            """
        )
        conn.execute("INSERT INTO orders (id, comment, version) VALUES (1, 'Первичный черновик', 1)")
    conn.close()


def get_order(db_path: Path, order_id: int) -> tuple[int, dict[str, Any]]:
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        row = conn.execute("SELECT id, comment, version FROM orders WHERE id = ?", (order_id,)).fetchone()
        if row is None:
            return 404, {"error": "order_not_found", "order_id": order_id}
        return 200, {"id": row[0], "comment": row[1], "version": row[2]}
    finally:
        conn.close()


def update_order_with_version(
    db_path: Path, order_id: int, expected_version: int, new_comment: str
) -> tuple[int, dict[str, Any]]:
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        with conn:
            cur = conn.execute(
                "UPDATE orders SET comment = ?, version = version + 1 WHERE id = ? AND version = ?",
                (new_comment, order_id, expected_version),
            )
            if cur.rowcount == 1:
                return 200, {"status": "saved", "new_version": expected_version + 1, "saved_comment": new_comment}

            actual = conn.execute("SELECT comment, version FROM orders WHERE id = ?", (order_id,)).fetchone()
            if actual is None:
                return 404, {"error": "order_not_found", "order_id": order_id}
            return 409, {
                "error": "version_conflict",
                "server_comment": actual[0],
                "server_version": actual[1],
                "client_draft": new_comment,
            }
    finally:
        conn.close()


class ConcurrentVersionConflictTests(unittest.TestCase):
    def test_missing_order_returns_404(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "orders.db"
            setup_db(db_path)
            code_get, body_get = get_order(db_path, 999)
            self.assertEqual(code_get, 404)
            code_upd, body_upd = update_order_with_version(db_path, 999, 1, "Текст")
            self.assertEqual(code_upd, 404)
            self.assertEqual(body_upd["error"], "order_not_found")

    def test_parallel_edits_produce_one_200_and_one_409_with_preserved_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "orders.db"
            setup_db(db_path)
            barrier = threading.Barrier(2, timeout=5.0)

            def client_flow(comment_text: str) -> tuple[int, dict[str, Any]]:
                status_read, initial = get_order(db_path, 1)
                assert status_read == 200
                barrier.wait(timeout=5.0)
                return update_order_with_version(db_path, 1, initial["version"], comment_text)

            payload_a = "Правка оператора А: доставка к 14:00"
            payload_b = "Правка оператора Б: добавить подъём на этаж"
            with ThreadPoolExecutor(max_workers=2) as pool:
                f_a = pool.submit(client_flow, payload_a)
                f_b = pool.submit(client_flow, payload_b)
                res_a = f_a.result(timeout=5.0)
                res_b = f_b.result(timeout=5.0)

            results = [res_a, res_b]
            codes = sorted(code for code, _ in results)
            self.assertEqual(codes, [200, 409])

            winner = next(body for code, body in results if code == 200)
            loser = next(body for code, body in results if code == 409)

            self.assertEqual(winner["new_version"], 2)
            for (code, response), input_text in ((res_a, payload_a), (res_b, payload_b)):
                field = "saved_comment" if code == 200 else "client_draft"
                self.assertEqual(response[field], input_text)
            self.assertEqual(loser["server_version"], 2)
            self.assertEqual(loser["server_comment"], winner["saved_comment"])

            final_code, final_row = get_order(db_path, 1)
            self.assertEqual(final_code, 200)
            self.assertEqual(final_row["version"], 2)
            self.assertEqual(final_row["comment"], winner["saved_comment"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
