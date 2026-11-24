# Спецификация и эксплуатационный регламент: идемпотентный приём платежа

> Граница артефакта: документ демонстрирует оформление HTTP-контракта и эксплуатационного runbook на обезличенном примере. Исполняемое ядро в парном файле `01_idempotency_and_reconcile_demo.py` моделирует резервирование ключа, валидацию входа, сетевой таймаут шлюза, локальную сверку и эскалацию в `manual_review` (HTTP-маршрут `GET`, терминальные банковские отказы `declined` и 5xx провайдера описаны здесь как часть полной спецификации интерфейса).

## 1. Контракт `POST /api/v1/payments`

### Обязательные заголовки
| Заголовок | Формат | Назначение |
|---|---|---|
| `Authorization` | `Bearer <jwt>` | Определяет `merchant_id`. Область действия ключа идемпотентности — пара `(merchant_id, Idempotency-Key)`, сериализуемая однозначно как JSON-массив. |
| `Idempotency-Key` | UUIDv4 (`36` символов) | Уникальный ключ попытки оплаты на стороне клиента (например, `6c84fb90-12c4-41ef-8a00-0242ac120002`). Записи незавершённых операций хранятся до финального статуса. |

### Запрос
```json
{
  "order_id": "ord_2026_08412",
  "amount_cents": 450000,
  "currency": "RUB"
}
```
Правила валидации и нормализации перед расчётом `payload_hash` (`SHA-256`): `order_id` — непустая строка без краевых пробелов, `amount_cents` — целое число в диапазоне `1 .. 10 000 000 000` (копейки), `currency` — код из белого списка (`RUB`, `USD`, `EUR`, `KZT`).

### Коды ответа и примеры тела

- **`201 Created`** — платёж проведён провайдером и зафиксирован в БД вместе с событием `outbox`:
```json
{"status": "succeeded", "merchant_ref": "9f84c2017ab41e09d12a331088ac1902", "provider_tx_id": "prov_0001"}
```
- **`202 Accepted`** — запрос зарезервирован, внешний провайдер не ответил до сетевого таймаута, запись передана в фоновую сверку:
```json
{"status": "pending_reconcile", "merchant_ref": "9f84c2017ab41e09d12a331088ac1902", "retry_safe": true}
```
- **`200 OK`** — повторный запрос с тем же `(merchant_id, Idempotency-Key)` и тем же `payload_hash` (возвращает актуальный статус БД: `reserved`, `pending_reconcile`, `succeeded` или `manual_review` без вызова `charge`):
```json
{"idempotent_replay": true, "status": "succeeded", "merchant_ref": "9f84c2017ab41e09d12a331088ac1902", "provider_tx_id": "prov_0001"}
```
- **`409 Conflict`** — тот же `(merchant_id, Idempotency-Key)` отправлен с изменённым `order_id`, `amount_cents` или `currency`:
```json
{"error": "idempotency_payload_mismatch", "detail": "Same Idempotency-Key used with different payment parameters"}
```
- **`422 Unprocessable Entity`** — ошибка валидации суммы, валюты или идентификатора заказа (ключ идемпотентности не резервируется):
```json
{"error": "validation_error", "detail": "amount_cents must be an integer between 1 and 10_000_000_000"}
```

### Чтение статуса `GET /api/v1/payments/{merchant_ref}`
Read-only метод чтения карточки платежа для клиента и дежурной смены. Возвращает `200 OK` с полями `status`, `order_id`, `amount_cents`, `currency`, `provider_tx_id` либо `404 Not Found`, если `merchant_ref` отсутствует у данного `merchant_id`.

---

## 2. Runbook дежурного инженера L2/L3: алерт `PaymentReconcileLagHigh`

### Симптом
В таблице `payments` присутствуют записи в статусах `pending_reconcile`, `manual_review` или `reserved` с истёкшим `lease_expires_at` дольше 10 минут.

### Запрещённые действия (защита от двойного списания)
1. **Запрещено** вызывать платёжный метод `charge` повторно вручную или удалять строку из `payments`.
2. **Запрещено** закрывать операцию как неуспешную и разрешать оплату с новым ключом на основании одиночного ответа `not_found` или `5xx` от API провайдера.

### Порядок действий
1. **Локальная read-only выборка** зависших записей:
       `SELECT id, merchant_id, merchant_ref, order_id, status, lease_expires_at FROM payments WHERE status IN ('pending_reconcile', 'manual_review') OR (status = 'reserved' AND lease_expires_at < strftime('%s','now'));`
2. **Внешняя read-only проверка у провайдера** по `merchant_ref` (без создания платёжной сессии).
3. **Применение регламентного перехода в локальной БД** (`reconcile_worker` для `pending_reconcile`/`reserved` и адресный `reconcile_manual_review_by_ref` для записей после ручного разбора `manual_review`):
   - Провайдер подтвердил `captured` -> штатная процедура сверки записывает в локальную БД `status = 'succeeded'`, `provider_tx_id` и создаёт запись в `outbox_events` по `aggregate_key = merchant_ref`.
   - Провайдер вернул подтверждённый терминальный отказ (`declined` / `canceled`) -> фиксируется код отказа провайдера; повторная оплата заказа допускается только с новым `Idempotency-Key` согласно регламенту провайдера.
   - Провайдер недоступен (`5xx`) или вернул `not_found` по отправленному `merchant_ref` -> запись переводится / остаётся в `manual_review`, повторная оплата не открывается, список `merchant_ref` передаётся в поддержку провайдера для сверки по реестру эквайринга.

### Критерий закрытия инцидента
Для проверенных `merchant_ref` локальный статус в `payments` соответствует документально подтверждённому статусу провайдера, на каждый успешный платёж в `outbox_events` присутствует ровно одна запись, дублирующих списаний нет.
