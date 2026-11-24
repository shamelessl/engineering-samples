"""Учебная модель доставки пачек. Запуск: python batch_replay.py.

Источник здесь неизменяемый список версий документов. Индекс списка изображает
подтверждённую позицию. Доступа к 1С, сетевым сервисам и клиентским данным нет.
"""
import sqlite3
import unittest


def deliver(db, documents):
    with db:
        for doc_id, revision, quantity in documents:
            db.execute(
                "INSERT INTO receipt VALUES (?, ?, ?) ON CONFLICT(doc_id, revision) DO NOTHING",
                (doc_id, revision, quantity),
            )
            saved = db.execute(
                "SELECT quantity FROM receipt WHERE doc_id=? AND revision=?",
                (doc_id, revision),
            ).fetchone()
            if saved[0] != quantity:
                raise ValueError("Для уже принятой версии получено другое количество")


class BatchReplay(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute(
            "CREATE TABLE receipt(doc_id INTEGER, revision INTEGER, quantity INTEGER,"
            "PRIMARY KEY(doc_id, revision))"
        )

    def test_lost_ack_after_first_fifty(self):
        source = [(doc_id, 1, 10) for doc_id in range(53)]
        position = 0
        first = source[position:position + 50]
        deliver(self.db, first)
        # Получатель сделал commit, отправитель остался без подтверждения.
        self.assertEqual(position, 0)
        deliver(self.db, source[position:position + 50])
        position += len(first)
        deliver(self.db, source[position:])
        saved = self.db.execute("SELECT * FROM receipt ORDER BY doc_id, revision").fetchall()
        self.assertEqual(saved, source)

    def test_new_revision_and_conflicting_duplicate(self):
        deliver(self.db, [(7, 1, 10), (7, 2, 12)])
        with self.assertRaises(ValueError):
            deliver(self.db, [(8, 1, 4), (7, 2, 99)])
        self.assertEqual(
            self.db.execute("SELECT * FROM receipt ORDER BY doc_id, revision").fetchall(),
            [(7, 1, 10), (7, 2, 12)],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
