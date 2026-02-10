"""Cross-platform, process-scoped experiment-directory locking."""

from __future__ import annotations

import json
import os
import socket
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from scaffoldscope.errors import ConfigError


def _lock(handle: BinaryIO) -> None:
    handle.seek(0)
    if sys.platform == "win32":
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: BinaryIO) -> None:
    handle.seek(0)
    if sys.platform == "win32":
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _owner_text(handle: BinaryIO) -> str:
    try:
        handle.seek(0)
        value = json.loads(handle.read().decode("utf-8", errors="replace"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "unknown owner"
    if not isinstance(value, dict):
        return "unknown owner"
    return f"pid={value.get('pid', '?')} host={value.get('host', '?')} since={value.get('acquired_at', '?')}"


@contextmanager
def experiment_lock(experiment_dir: Path) -> Iterator[None]:
    """Hold an OS-released exclusive lock for one experiment operation."""

    root = experiment_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".scaffoldscope.lock"
    try:
        handle = lock_path.open("a+b")
    except OSError as exc:
        raise ConfigError(f"Could not open experiment lock {lock_path}: {exc}") from exc
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        try:
            _lock(handle)
        except OSError as exc:
            owner = _owner_text(handle)
            raise ConfigError(
                f"Experiment is already active: {root} ({owner}). "
                "Wait for that process to finish before accessing this experiment again."
            ) from exc
        metadata = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        }
        handle.seek(0)
        handle.truncate()
        handle.write((json.dumps(metadata, sort_keys=True) + "\n").encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
        try:
            yield
        finally:
            _unlock(handle)
    finally:
        handle.close()
