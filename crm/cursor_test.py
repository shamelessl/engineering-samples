"""Поздние изменения: курсор по порядку журнала, бизнес-дата может повторяться."""
from importlib import import_module
import sqlite3
import unittest

deliver = import_module("batch_replay").deliver


def page(source, cursor, limit):
    return source.execute(
        "SELECT seq, doc_id, revision, quantity FROM changes WHERE seq > ? ORDER BY seq LIMIT ?",
        (cursor, limit),
    ).fetchall()


class LateChangesTest(unittest.TestCase):
    def test_late_revision_survives_replay_across_page_boundary(self):
        src = sqlite3.connect(":memory:")
        dest = sqlite3.connect(":memory:")
        self.addCleanup(src.close)
        self.addCleanup(dest.close)
        src.execute("CREATE TABLE changes(seq INTEGER PRIMARY KEY, day TEXT, doc_id, revision, quantity)")
        src.executemany("INSERT INTO changes VALUES (?, '2025-03-01', ?, 1, 12)",
                        [(i, i) for i in range(1, 52)])
        dest.execute("CREATE TABLE receipt(doc_id, revision, quantity, PRIMARY KEY(doc_id,revision))")
        cursor = 0
        first = page(src, cursor, 50)
        deliver(dest, [r[1:] for r in first])  # подтверждение потерялось
        src.execute("INSERT INTO changes VALUES (52,'2025-02-28',2,2,18)")
        while batch := page(src, cursor, 50):
            deliver(dest, [r[1:] for r in batch])
            cursor = batch[-1][0]
        self.assertEqual(cursor, 52)
        self.assertEqual(dest.execute("SELECT count(*) FROM receipt").fetchone(), (52,))
        self.assertEqual(dest.execute("SELECT quantity FROM receipt WHERE doc_id=2 AND revision=2").fetchone(), (18,))


if __name__ == "__main__":
    unittest.main(verbosity=2)
