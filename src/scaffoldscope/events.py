"""Append-only, redacted JSONL traces."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scaffoldscope.jsonutil import canonical_json
from scaffoldscope.redact import redact


class EventLog:
    def __init__(self, path: Path, *, redact_secrets: bool = True) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.redact_secrets = redact_secrets
        self.sequence = 0
        self._lock = threading.Lock()

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        with self._lock:
            sequence = self.sequence + 1
            event = {
                "schema_version": 1,
                "sequence": sequence,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": event_type,
                "payload": redact(payload) if self.redact_secrets else payload,
            }
            encoded = canonical_json(event) + "\n"
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)
                handle.flush()
            self.sequence = sequence
