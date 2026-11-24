-- Самостоятельный воспроизводимый SQL-пример (SQLite 3.35+):
-- Дневная витрина выручки на утренний срез 07:00 по исторической цене прайса (SCD Type 2),
-- строгая проверка 19-символьной календарной ISO-даты 'YYYY-MM-DDTHH:MM:SS',
-- карантин отсутствующих/перекрывающихся тарифов (с привязкой к дате загрузки пачки ingest_batch_date)
-- и маркировка 'incomplete_source', если выгрузка филиала не поступила к дедлайну expected_by.

PRAGMA foreign_keys = ON;

CREATE TABLE branch_ingest_log (
    report_date TEXT NOT NULL,
    branch_id INTEGER NOT NULL,
    expected_by TEXT NOT NULL,
    received_at TEXT,
    PRIMARY KEY (report_date, branch_id)
);

CREATE TABLE service_tariff_scd2 (
    tariff_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_code TEXT NOT NULL,
    price_rub INTEGER NOT NULL CHECK (price_rub > 0),
    valid_from TEXT NOT NULL,
    valid_to TEXT
);

CREATE TABLE raw_visits (
    visit_id TEXT PRIMARY KEY,
    ingest_batch_date TEXT NOT NULL,
    branch_id INTEGER NOT NULL,
    service_code TEXT NOT NULL,
    visit_ts TEXT,
    status TEXT NOT NULL
);

-- Тестовый набор:
-- Филиал 1 сдал файл вовремя (06:42 <= 07:00); филиал 2 не сдал (NULL); филиал 3 опоздал к утреннему срезу (12:05 > 07:00).
INSERT INTO branch_ingest_log VALUES
    ('2026-01-20', 1, '2026-01-21T07:00:00', '2026-01-21T06:30:00'),
    ('2026-03-10', 1, '2026-03-11T07:00:00', '2026-03-11T06:42:00'),
    ('2026-03-10', 2, '2026-03-11T07:00:00', NULL),
    ('2026-03-10', 3, '2026-03-11T07:00:00', '2026-03-11T12:05:00');

INSERT INTO service_tariff_scd2 (service_code, price_rub, valid_from, valid_to) VALUES
    ('CONS_01', 2200, '2026-01-01T00:00:00', '2026-03-01T00:00:00'),
    ('CONS_01', 2900, '2026-03-01T00:00:00', NULL),
    ('MRT_BAD', 5000, '2026-01-01T00:00:00', '2026-04-01T00:00:00'),
    ('MRT_BAD', 5800, '2026-03-01T00:00:00', NULL);

INSERT INTO raw_visits VALUES
    ('v_jan_old_price',   '2026-01-20', 1, 'CONS_01', '2026-01-20T11:30:00', 'completed'),
    ('v_mar_1',           '2026-03-10', 1, 'CONS_01', '2026-03-10T10:15:00', 'completed'),
    ('v_mar_2',           '2026-03-10', 1, 'CONS_01', '2026-03-10T14:40:00', 'completed'),
    ('v_late_branch3',    '2026-03-10', 3, 'CONS_01', '2026-03-10T15:00:00', 'completed'),
    ('v_bad_null_ts',     '2026-03-10', 1, 'CONS_01', NULL,                  'completed'),
    ('v_bad_feb30',       '2026-03-10', 1, 'CONS_01', '2026-02-30T10:00:00', 'completed'),
    ('v_bad_hour24',      '2026-03-10', 1, 'CONS_01', '2026-03-10T24:15:00', 'completed'),
    ('v_bad_subsec',      '2026-03-10', 1, 'CONS_01', '2026-03-10T10:00:00.999', 'completed'),
    ('v_bad_garbage',     '2026-03-10', 1, 'CONS_01', 'not-a-date',          'completed'),
    ('v_future_ts',       '2026-03-10', 1, 'CONS_01', '2026-12-31T09:00:00', 'completed'),
    ('v_backdated_cross', '2026-03-10', 1, 'CONS_01', '2026-01-20T16:00:00', 'completed'),
    ('v_overlap_price',   '2026-03-10', 1, 'MRT_BAD', '2026-03-10T12:00:00', 'completed'),
    ('v_missing_price',   '2026-03-10', 1, 'UNKNOWN', '2026-03-10T13:00:00', 'completed');

-- 1. Классификация визитов: точный формат YYYY-MM-DDTHH:MM:SS (19 символов, часы 00..23, без переноса суток),
-- принадлежность к дате текущей пачки ingest_batch_date и однозначность исторического тарифа
CREATE VIEW vw_classified_visits AS
WITH date_checked AS (
    SELECT
        v.visit_id,
        v.ingest_batch_date,
        v.branch_id,
        v.service_code,
        v.visit_ts,
        v.status,
        CASE
            WHEN v.visit_ts IS NULL THEN 'NULL_TIMESTAMP'
            WHEN length(v.visit_ts) != 19
              OR v.visit_ts NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]'
              OR substr(v.visit_ts, 12, 2) NOT BETWEEN '00' AND '23'
              OR substr(v.visit_ts, 15, 2) NOT BETWEEN '00' AND '59'
              OR substr(v.visit_ts, 18, 2) NOT BETWEEN '00' AND '59'
              OR strftime('%Y-%m-%dT%H:%M:%S', v.visit_ts, '+0 seconds') IS NULL
              OR strftime('%Y-%m-%dT%H:%M:%S', v.visit_ts, '+0 seconds') != v.visit_ts
                THEN 'INVALID_CALENDAR_TIMESTAMP'
            WHEN substr(v.visit_ts, 1, 10) > v.ingest_batch_date
                THEN 'FUTURE_TIMESTAMP'
            WHEN substr(v.visit_ts, 1, 10) < v.ingest_batch_date
                THEN 'BACKDATED_CROSS_BATCH'
            ELSE 'DATE_OK'
        END AS ts_check
    FROM raw_visits v
),
tariff_matched AS (
    SELECT
        d.*,
        COUNT(t.tariff_row_id) AS tariff_matches,
        MIN(t.price_rub) AS matched_price_rub
    FROM date_checked d
    LEFT JOIN service_tariff_scd2 t
      ON d.ts_check = 'DATE_OK'
     AND t.service_code = d.service_code
     AND d.visit_ts >= t.valid_from
     AND (t.valid_to IS NULL OR d.visit_ts < t.valid_to)
    GROUP BY d.visit_id, d.ingest_batch_date, d.branch_id, d.service_code, d.visit_ts, d.status, d.ts_check
)
SELECT
    visit_id,
    ingest_batch_date,
    branch_id,
    service_code,
    visit_ts,
    status,
    CASE
        WHEN ts_check != 'DATE_OK' THEN ts_check
        WHEN tariff_matches = 0 THEN 'MISSING_HISTORICAL_TARIFF'
        WHEN tariff_matches > 1 THEN 'OVERLAPPING_TARIFF_INTERVALS'
        ELSE 'VALID'
    END AS dq_status,
    CASE
        WHEN ts_check = 'DATE_OK' AND tariff_matches = 1 THEN matched_price_rub
        ELSE NULL
    END AS historical_price_rub
FROM tariff_matched;

-- 2. Витрина карантина
CREATE VIEW vw_quarantine_visits AS
SELECT visit_id, ingest_batch_date, branch_id, service_code, visit_ts, dq_status
FROM vw_classified_visits
WHERE dq_status != 'VALID';

-- 3. Дневная витрина выручки на момент дедлайна expected_by (строго по пачке своей отчётной даты,
-- чтобы задним числом присланные строки чужого месяца уходили в карантин и не меняли закрытый срез)
CREATE VIEW vw_daily_branch_revenue AS
SELECT
    b.report_date,
    b.branch_id,
    CASE
        WHEN b.received_at IS NULL OR b.received_at > b.expected_by THEN 'incomplete_source'
        ELSE 'complete'
    END AS slice_status,
    CASE
        WHEN b.received_at IS NULL OR b.received_at > b.expected_by THEN NULL
        ELSE COALESCE(SUM(CASE WHEN c.dq_status = 'VALID' AND c.status = 'completed' THEN c.historical_price_rub ELSE 0 END), 0)
    END AS revenue_rub,
    COUNT(CASE WHEN c.dq_status != 'VALID' THEN 1 END) AS quarantined_rows_in_batch
FROM branch_ingest_log b
LEFT JOIN vw_classified_visits c
  ON c.branch_id = b.branch_id
 AND c.ingest_batch_date = b.report_date
GROUP BY b.report_date, b.branch_id, b.expected_by, b.received_at;
