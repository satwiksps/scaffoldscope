from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scaffoldscope.errors import ConfigError
from scaffoldscope.replay import replay_trial


class ReplayTests(unittest.TestCase):
    def test_replay_is_offline_hash_checked_and_human_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            experiment = Path(temporary) / "run"
            trial = experiment / "trials" / "trial-a"
            trial.mkdir(parents=True)
            events = [
                {
                    "sequence": 1,
                    "timestamp": "2026-08-15T00:00:00+00:00",
                    "type": "tool_result",
                    "payload": {
                        "turn": 1,
                        "tool": "read_file",
                        "ok": True,
                        "duration_seconds": 0.01,
                    },
                },
                {
                    "sequence": 2,
                    "timestamp": "2026-08-15T00:00:01+00:00",
                    "type": "trial_finished",
                    "payload": {"status": "resolved", "solved": True},
                },
            ]
            trace = "".join(json.dumps(event) + "\n" for event in events).encode()
            (trial / "events.jsonl").write_bytes(trace)
            result = {
                "trial_id": "trial-a",
                "task_id": "task-a",
                "variant_id": "selective",
                "replicate": 7,
                "status": "resolved",
                "solved": True,
                "artifact_hashes": {"trace_sha256": hashlib.sha256(trace).hexdigest()},
            }
            (trial / "result.json").write_text(json.dumps(result), encoding="utf-8")

            replay = replay_trial(experiment, "trial-a")

            self.assertEqual(replay["event_count"], 2)
            self.assertIn("tool read_file (ok", replay["timeline"][0]["summary"])
            self.assertIn("trial finished", replay["timeline"][1]["summary"])

            events[0]["payload"]["ok"] = False
            (trial / "events.jsonl").write_text(
                "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
            )
            with self.assertRaisesRegex(ConfigError, "hash"):
                replay_trial(experiment, "trial-a")


if __name__ == "__main__":
    unittest.main()
