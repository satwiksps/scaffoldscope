import unittest

from text_cleaner import collapse_spaces


class CollapseSpacesTests(unittest.TestCase):
    def test_long_space_run(self):
        self.assertEqual(collapse_spaces("one    two"), "one two")

    def test_mixed_whitespace(self):
        self.assertEqual(collapse_spaces(" one\t\n two "), "one two")


if __name__ == "__main__":
    unittest.main()
