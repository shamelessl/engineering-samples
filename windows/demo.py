"""Разбор синтетического журнала. Не обращается к процессам Windows."""
from dataclasses import dataclass
import unittest


@dataclass(frozen=True)
class Identity:
    pid: int
    tid: int
    process_start: int
    thread_start: int


def recovery_decision(original, observed, acknowledged):
    if original != observed:
        return "skip: different owner"
    if not acknowledged:
        return "skip: operation unconfirmed"
    return "eligible for restoration"


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.owner = Identity(100, 42, 1000, 1001)

    def test_same_identity_and_confirmation(self):
        self.assertEqual(recovery_decision(self.owner, self.owner, True), "eligible for restoration")

    def test_same_ids_different_start(self):
        new = Identity(100, 42, 2000, 2001)
        self.assertEqual(recovery_decision(self.owner, new, True), "skip: different owner")

    def test_thread_reused_inside_living_process(self):
        new = Identity(100, 42, 1000, 2001)
        self.assertEqual(recovery_decision(self.owner, new, True), "skip: different owner")

    def test_missing_confirmation(self):
        self.assertEqual(recovery_decision(self.owner, self.owner, False), "skip: operation unconfirmed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
