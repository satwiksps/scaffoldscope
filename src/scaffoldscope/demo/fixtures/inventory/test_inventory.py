import unittest

from inventory import cart_total


class InventoryTests(unittest.TestCase):
    def test_multiple_items(self):
        self.assertEqual(cart_total([10, 5, 2]), 17)

    def test_empty_cart(self):
        self.assertEqual(cart_total([]), 0)


if __name__ == "__main__":
    unittest.main()
