from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scaffoldscope.errors import ConfigError
from scaffoldscope.locking import experiment_lock


class ExperimentLockTests(unittest.TestCase):
    def test_lock_rejects_a_second_writer_and_releases_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            experiment = Path(temporary) / "run"
            with (
                experiment_lock(experiment),
                self.assertRaisesRegex(ConfigError, "already active"),
                experiment_lock(experiment),
            ):
                self.fail("a second writer acquired the experiment lock")

            metadata = json.loads((experiment / ".scaffoldscope.lock").read_text(encoding="utf-8"))
            self.assertIsInstance(metadata["pid"], int)

            with experiment_lock(experiment):
                pass


if __name__ == "__main__":
    unittest.main()
