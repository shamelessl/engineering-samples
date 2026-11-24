"""
Автотесты к SQL-витрине `02_scd2_historical_pricing_and_dq.sql` (запуск: `python3 02_test_scd2_sql.py`).
Проверяет историческую цену [valid_from, valid_to), карантин невалидных/дробных дат, изоляцию NULL-дат
внутри своей пачки загрузки, защиту от удвоения при перекрытии тарифов и учёт дедлайна expected_by.
"""
from __future__ import annotations

import pathlib
import sqlite3
import unittest

SQL_PATH = pathlib.Path(__file__).with_name("02_scd2_historical_pricing_and_dq.sql")


class Scd2SqlMartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SQL_PATH.read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self.conn.close()

    def test_historical_january_slice_clean_and_not_polluted_by_march_nulls(self) -> None:
        jan_mart = self.conn.execute(
            "SELECT * FROM vw_daily_branch_revenue WHERE report_date = '2026-01-20' AND branch_id = 1"
        ).fetchone()
        self.assertEqual(jan_mart["slice_status"], "complete")
        self.assertEqual(jan_mart["revenue_rub"], 2200)
        self.assertEqual(jan_mart["quarantined_rows_in_batch"], 0)

    def test_invalid_dates_hour24_subseconds_and_tariff_anomalies_quarantined(self) -> None:
        rows = {
            r["visit_id"]: r["dq_status"]
            for r in self.conn.execute("SELECT visit_id, dq_status FROM vw_quarantine_visits").fetchall()
        }
        self.assertEqual(rows["v_bad_null_ts"], "NULL_TIMESTAMP")
        self.assertEqual(rows["v_bad_feb30"], "INVALID_CALENDAR_TIMESTAMP")
        self.assertEqual(rows["v_bad_hour24"], "INVALID_CALENDAR_TIMESTAMP")
        self.assertEqual(rows["v_bad_subsec"], "INVALID_CALENDAR_TIMESTAMP")
        self.assertEqual(rows["v_bad_garbage"], "INVALID_CALENDAR_TIMESTAMP")
        self.assertEqual(rows["v_future_ts"], "FUTURE_TIMESTAMP")
        self.assertEqual(rows["v_backdated_cross"], "BACKDATED_CROSS_BATCH")
        self.assertEqual(rows["v_overlap_price"], "OVERLAPPING_TARIFF_INTERVALS")
        self.assertEqual(rows["v_missing_price"], "MISSING_HISTORICAL_TARIFF")

    def test_march_slice_respects_expected_by_deadline_and_overlap_guard(self) -> None:
        mart = {
            r["branch_id"]: dict(r)
            for r in self.conn.execute(
                "SELECT * FROM vw_daily_branch_revenue WHERE report_date = '2026-03-10' ORDER BY branch_id"
            ).fetchall()
        }
        self.assertEqual(mart[1]["slice_status"], "complete")
        self.assertEqual(mart[1]["revenue_rub"], 5800)
        self.assertEqual(mart[1]["quarantined_rows_in_batch"], 9)

        self.assertEqual(mart[2]["slice_status"], "incomplete_source")
        self.assertIsNone(mart[2]["revenue_rub"])

        # Филиал 3 прислал файл в 12:05 после утреннего дедлайна 07:00 -> на утренний срез помечен incomplete_source
        self.assertEqual(mart[3]["slice_status"], "incomplete_source")
        self.assertIsNone(mart[3]["revenue_rub"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
