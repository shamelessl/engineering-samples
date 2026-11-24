"""Минимальная модель старой вкладки Mini App на двух вымышленных группах."""
import sqlite3
import unittest


def add_task(db, student, screen_group, title):
    with db:
        count = db.execute("""
            INSERT INTO tasks (group_id, title)
            SELECT current_group, ? FROM students
            WHERE id = ? AND current_group = ?
              AND EXISTS (SELECT 1 FROM members WHERE student_id = students.id
                          AND group_id = students.current_group)
        """, (title, student, screen_group)).rowcount
    return count


class StaleTabTest(unittest.TestCase):
    def test_group_switch_prevents_old_form_writing(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.executescript("""
            CREATE TABLE students (id INTEGER PRIMARY KEY, current_group INTEGER);
            CREATE TABLE members (student_id INTEGER, group_id INTEGER);
            CREATE TABLE tasks (group_id INTEGER, title TEXT);
            INSERT INTO students VALUES (1, 10);
            INSERT INTO members VALUES (1, 10), (1, 20);
        """)
        self.assertEqual(add_task(db, 1, 10, "Алгебра"), 1)
        db.execute("UPDATE students SET current_group = 20 WHERE id = 1")
        self.assertEqual(add_task(db, 1, 10, "Из старой вкладки"), 0)
        self.assertEqual(db.execute("SELECT * FROM tasks").fetchall(), [(10, "Алгебра")])
        self.assertEqual(add_task(db, 1, 20, "Семинар"), 1)
        db.execute("DELETE FROM members WHERE student_id = 1 AND group_id = 20")
        self.assertEqual(add_task(db, 1, 20, "После выхода из группы"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
