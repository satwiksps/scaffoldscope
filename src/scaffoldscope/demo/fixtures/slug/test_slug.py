import unittest

from slug import slugify


class SlugTests(unittest.TestCase):
    def test_single_space(self):
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_whitespace_run(self):
        self.assertEqual(slugify("Hello   World"), "hello-world")

    def test_tabs(self):
        self.assertEqual(slugify("Hello\tWorld"), "hello-world")


if __name__ == "__main__":
    unittest.main()
