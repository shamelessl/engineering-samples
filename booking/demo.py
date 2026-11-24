"""Проверка предложения слота в момент подтверждения. Python 3.10+."""
import sqlite3
import unittest


def confirm(db, slot, shown_price):
    with db:
        written = db.execute(
            "UPDATE slots SET booked = 1 WHERE id = ? AND price = ? AND booked = 0",
            (slot, shown_price),
        ).rowcount
    return "confirmed" if written == 1 else "refresh_offer"


class BookingTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("CREATE TABLE slots (id INTEGER PRIMARY KEY, price INTEGER, booked INTEGER)")
        self.db.execute("INSERT INTO slots VALUES (1, 2200, 0)")

    def test_two_clients_with_the_same_offer(self):
        self.assertEqual(confirm(self.db, 1, 2200), "confirmed")
        self.assertEqual(confirm(self.db, 1, 2200), "refresh_offer")

    def test_price_changed_after_offer(self):
        self.db.execute("UPDATE slots SET price = 2400 WHERE id = 1")
        self.assertEqual(confirm(self.db, 1, 2200), "refresh_offer")
        self.assertEqual(self.db.execute("SELECT booked FROM slots").fetchone(), (0,))


if __name__ == "__main__":
    unittest.main(verbosity=2)
