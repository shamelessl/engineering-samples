"""Сравнение масок на пиксельной сетке с точным совпадением контура."""
import unittest


def boundary(mask):
    return {(x, y) for x, y in mask if any(
        (x + dx, y + dy) not in mask for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0))
    )}


def scores(prediction, target):
    union = prediction | target
    a, b = boundary(prediction), boundary(target)
    iou = len(prediction & target) / len(union) if union else 1.0
    contour_f1 = 2 * len(a & b) / (len(a) + len(b)) if a or b else 1.0
    return iou, contour_f1


class MaskTest(unittest.TestCase):
    def test_area_and_boundary_rank_models_differently(self):
        target = {(x, y) for x in range(10) for y in range(10)}
        incomplete = {(x, y) for x in range(5) for y in range(10)}
        expanded = {(x, y) for x in range(-1, 11) for y in range(-1, 11)}
        old, new = scores(incomplete, target), scores(expanded, target)
        self.assertGreater(new[0], old[0])
        self.assertLess(new[1], old[1])
        print(f"IoU {old[0]:.3f} -> {new[0]:.3f}; boundary F1 {old[1]:.3f} -> {new[1]:.3f}")

    def test_identical_and_empty(self):
        self.assertEqual(scores({(1, 2)}, {(1, 2)}), (1.0, 1.0))
        self.assertEqual(scores(set(), set()), (1.0, 1.0))
        self.assertEqual(scores(set(), {(1, 2)}), (0.0, 0.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
