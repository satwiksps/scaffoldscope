from __future__ import annotations

import unittest

from scaffoldscope.agent import parse_response
from scaffoldscope.errors import ProtocolError


class ProtocolTests(unittest.TestCase):
    def test_parses_action(self) -> None:
        parsed = parse_response('{"action":{"tool":"read_file","arguments":{"path":"hello.py"}}}')
        self.assertIsNotNone(parsed.action)
        assert parsed.action is not None
        self.assertEqual(parsed.action.tool, "read_file")
        self.assertEqual(parsed.action.arguments["path"], "hello.py")

    def test_parses_fenced_json_for_provider_tolerance(self) -> None:
        parsed = parse_response('```json\n{"final":"done"}\n```')
        self.assertEqual(parsed.final, "done")

    def test_rejects_ambiguous_response(self) -> None:
        with self.assertRaises(ProtocolError):
            parse_response('{"action":{},"final":"done"}')

    def test_rejects_prose_wrappers_and_unknown_fields(self) -> None:
        with self.assertRaises(ProtocolError):
            parse_response('Here you go: {"final":"done"}')
        with self.assertRaises(ProtocolError):
            parse_response('{"final":"done","confidence":1}')


if __name__ == "__main__":
    unittest.main()
