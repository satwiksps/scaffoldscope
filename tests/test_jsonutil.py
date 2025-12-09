from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scaffoldscope.errors import ConfigError
from scaffoldscope.events import EventLog
from scaffoldscope.jsonutil import atomic_write_json, canonical_json, load_json, load_jsonl


class JsonInputTests(unittest.TestCase):
    def test_json_and_jsonl_accept_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            json_path = root / "input.json"
            jsonl_path = root / "input.jsonl"
            json_path.write_bytes(b'\xef\xbb\xbf{"value":1}\n')
            jsonl_path.write_bytes(b'\xef\xbb\xbf{"value":1}\n{"value":2}\n')

            self.assertEqual(load_json(json_path), {"value": 1})
            self.assertEqual(load_jsonl(jsonl_path), [{"value": 1}, {"value": 2}])

    def test_nonfinite_numbers_are_rejected_on_read_and_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            json_path = root / "invalid.json"
            jsonl_path = root / "invalid.jsonl"
            output_path = root / "output.json"
            trace_path = root / "events.jsonl"
            json_path.write_text('{"value":NaN}', encoding="utf-8")
            jsonl_path.write_text('{"value":Infinity}\n', encoding="utf-8")
            overflow_path = root / "overflow.json"
            overflow_path.write_text('{"value":1e999}', encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "non-finite JSON number"):
                load_json(json_path)
            with self.assertRaisesRegex(ConfigError, "non-finite JSON number"):
                load_jsonl(jsonl_path)
            with self.assertRaisesRegex(ConfigError, "finite float range"):
                load_json(overflow_path)
            with self.assertRaises(ValueError):
                canonical_json({"value": float("nan")})
            with self.assertRaises(ValueError):
                atomic_write_json(output_path, {"value": float("inf")})
            self.assertFalse(output_path.exists())

            events = EventLog(trace_path, redact_secrets=False)
            with self.assertRaises(ValueError):
                events.emit("invalid", {"value": float("nan")})
            events.emit("valid", {"value": 1})
            self.assertEqual(load_jsonl(trace_path)[0]["sequence"], 1)


if __name__ == "__main__":
    unittest.main()
