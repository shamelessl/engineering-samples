"""Сопоставляет схему и журнал собственного условного desktop-приложения."""
import unittest


def compare(contract, trace):
    seen = set()
    differences = []
    for method, result in trace:
        seen.add(method)
        if method not in contract:
            differences.append((method, "missing from contract"))
        elif type(result) is not contract[method]:
            differences.append((method, "unexpected result type"))
    return sorted(contract.keys() - seen), differences


class ContractTest(unittest.TestCase):
    def test_declared_observed_and_changed_are_distinct(self):
        spec = {"document.count": int, "document.title": str, "view.zoom": int}
        observed = [("document.count", 2), ("document.title", None), ("view.ready", True)]
        missing, differences = compare(spec, observed)
        self.assertEqual(missing, ["view.zoom"])
        self.assertEqual(differences, [
            ("document.title", "unexpected result type"),
            ("view.ready", "missing from contract"),
        ])

    def test_bool_is_not_an_integer_in_this_wire_contract(self):
        self.assertEqual(compare({"count": int}, [("count", True)])[1],
                         [("count", "unexpected result type")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
