import unittest

from calculator import add


class CalculatorTests(unittest.TestCase):
    def test_positive_numbers(self):
        self.assertEqual(add(2, 3), 5)

    def test_negative_number(self):
        self.assertEqual(add(-2, 3), 1)


if __name__ == "__main__":
    unittest.main()
