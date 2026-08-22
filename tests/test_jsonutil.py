from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import TextIO
from unittest.mock import patch

from scaffoldscope.errors import ConfigError
from scaffoldscope.events import EventLog
from scaffoldscope.jsonutil import (
    atomic_write_json,
    atomic_write_text,
    canonical_json,
    load_json,
    load_jsonl,
)


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


class _WriteFailingTextFile:
    def __init__(self, handle: TextIO) -> None:
        self.handle = handle

    def __enter__(self) -> _WriteFailingTextFile:
        return self

    def __exit__(self, *_args: object) -> None:
        self.handle.close()

    def write(self, _text: str) -> int:
        raise OSError("injected write failure")


class AtomicWriteTextFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.target = self.root / "state.txt"
        self.target.write_text("old state\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _assert_old_target_and_no_temporary_file(self) -> None:
        self.assertEqual(self.target.read_text(encoding="utf-8"), "old state\n")
        self.assertEqual(list(self.root.glob(f".{self.target.name}.*")), [])

    def test_write_failure_preserves_target_and_removes_temporary_file(self) -> None:
        real_fdopen = os.fdopen

        def failing_fdopen(*args: object, **kwargs: object) -> _WriteFailingTextFile:
            return _WriteFailingTextFile(real_fdopen(*args, **kwargs))

        with (
            patch("scaffoldscope.jsonutil.os.fdopen", side_effect=failing_fdopen),
            self.assertRaisesRegex(OSError, "injected write failure"),
        ):
            atomic_write_text(self.target, "new state\n")

        self._assert_old_target_and_no_temporary_file()

    def test_fsync_failure_preserves_target_and_removes_temporary_file(self) -> None:
        with (
            patch(
                "scaffoldscope.jsonutil.os.fsync",
                side_effect=OSError("injected fsync failure"),
            ),
            self.assertRaisesRegex(OSError, "injected fsync failure"),
        ):
            atomic_write_text(self.target, "new state\n")

        self._assert_old_target_and_no_temporary_file()

    def test_replace_failure_preserves_target_and_removes_temporary_file(self) -> None:
        with (
            patch(
                "scaffoldscope.jsonutil.os.replace",
                side_effect=OSError("injected replace failure"),
            ),
            self.assertRaisesRegex(OSError, "injected replace failure"),
        ):
            atomic_write_text(self.target, "new state\n")

        self._assert_old_target_and_no_temporary_file()


if __name__ == "__main__":
    unittest.main()
