"""Safe, idempotent creation of a first ScaffoldScope experiment.

The starter is deliberately an initializer, not a template synchronizer. Once a
project has been created, rerunning the initializer fills in missing managed files
but never overwrites files the operator may have edited.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any

from scaffoldscope.errors import ScaffoldScopeError

__all__ = ["StarterError", "StarterProject", "create_starter_project"]

_FORMAT_VERSION = 1
_MARKER = ".scaffoldscope-project.json"
_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
_NAME_PLACEHOLDER = b"{{EXPERIMENT_NAME}}"
_ASSET_PATHS = (
    ".gitignore",
    "README.md",
    "experiment.json",
    "tasks.jsonl",
    "workspaces/text-cleaner/text_cleaner.py",
    "workspaces/text-cleaner/test_text_cleaner.py",
)


class StarterError(ScaffoldScopeError):
    """A starter project could not be created without risking user files."""


@dataclass(frozen=True)
class StarterProject:
    """Outcome of creating or revisiting a starter project."""

    root: Path
    config_path: Path
    created_files: tuple[Path, ...]
    preserved_files: tuple[Path, ...]

    @property
    def initialized(self) -> bool:
        """Whether this call wrote at least one file."""

        return bool(self.created_files)


def _validate_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME_PATTERN.fullmatch(name):
        raise StarterError(
            "experiment name must start with an alphanumeric character, contain only "
            "letters, numbers, '.', '_', or '-', and be at most 128 characters"
        )
    return name


def _asset_bytes(relative: str) -> bytes:
    resource = files("scaffoldscope").joinpath("starter_assets", *PurePosixPath(relative).parts)
    try:
        return resource.read_bytes()
    except (FileNotFoundError, OSError) as exc:
        raise StarterError(
            f"starter asset {relative!r} is missing from this ScaffoldScope installation"
        ) from exc


def _rendered_files(name: str) -> dict[PurePosixPath, bytes]:
    rendered: dict[PurePosixPath, bytes] = {}
    encoded_name = name.encode("utf-8")
    for item in _ASSET_PATHS:
        relative = PurePosixPath(item)
        content = _asset_bytes(item)
        if item == "experiment.json":
            if content.count(_NAME_PLACEHOLDER) != 1:
                raise StarterError("starter experiment template has an invalid name placeholder")
            content = content.replace(_NAME_PLACEHOLDER, encoded_name)
        elif _NAME_PLACEHOLDER in content:
            raise StarterError(f"unexpected experiment-name placeholder in starter asset {item!r}")
        rendered[relative] = content
    return rendered


def _marker_bytes(name: str) -> bytes:
    value = {
        "experiment_name": name,
        "format_version": _FORMAT_VERSION,
        "generated_files": list(_ASSET_PATHS),
        "tool": "scaffoldscope",
    }
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _load_marker(path: Path, *, expected_name: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise StarterError(f"starter marker is not a regular file: {path}")
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StarterError(f"could not read starter marker {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("tool") != "scaffoldscope":
        raise StarterError(f"directory has an unrecognized starter marker: {path}")
    if value.get("format_version") != _FORMAT_VERSION:
        raise StarterError(
            f"starter marker format {value.get('format_version')!r} is not supported"
        )
    if value.get("experiment_name") != expected_name:
        raise StarterError(
            "directory was initialized for experiment "
            f"{value.get('experiment_name')!r}, not {expected_name!r}"
        )


def _known_directories(rendered: dict[PurePosixPath, bytes]) -> set[PurePosixPath]:
    directories: set[PurePosixPath] = set()
    for relative in rendered:
        directories.update(relative.parents)
    directories.discard(PurePosixPath("."))
    return directories


def _verify_interrupted_scaffold(
    root: Path,
    rendered: dict[PurePosixPath, bytes],
) -> None:
    """Recognize files left by an interrupted marker-last initialization."""

    known_directories = _known_directories(rendered)
    for item in root.rglob("*"):
        relative = PurePosixPath(item.relative_to(root).as_posix())
        if item.is_symlink():
            raise StarterError(f"refusing to adopt a scaffold containing a symlink: {item}")
        if item.is_dir():
            if relative not in known_directories:
                raise StarterError(
                    f"destination is not empty and is not a ScaffoldScope starter: {item}"
                )
            continue
        expected = rendered.get(relative)
        if expected is None:
            raise StarterError(
                f"destination is not empty and is not a ScaffoldScope starter: {item}"
            )
        try:
            actual = item.read_bytes()
        except OSError as exc:
            raise StarterError(f"could not inspect existing file {item}: {exc}") from exc
        if actual != expected:
            raise StarterError(
                f"destination contains a conflicting unowned file; refusing to overwrite: {item}"
            )


def _preflight_targets(
    root: Path,
    rendered: dict[PurePosixPath, bytes],
    *,
    managed: bool,
) -> None:
    for relative, expected in rendered.items():
        target = root.joinpath(*relative.parts)
        parent = target.parent
        while parent != root:
            if parent.exists() and (parent.is_symlink() or not parent.is_dir()):
                raise StarterError(f"starter directory path is not a regular directory: {parent}")
            parent = parent.parent
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise StarterError(f"starter file path is not a regular file: {target}")
        if target.exists() and not managed:
            try:
                if target.read_bytes() != expected:
                    raise StarterError(f"destination contains a conflicting unowned file: {target}")
            except OSError as exc:
                raise StarterError(f"could not inspect existing file {target}: {exc}") from exc


def _write_new_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(content)
    except FileExistsError as exc:
        raise StarterError(f"starter target appeared while initializing: {path}") from exc
    except OSError as exc:
        raise StarterError(f"could not create starter file {path}: {exc}") from exc


def create_starter_project(
    destination: Path,
    *,
    name: str = "my-context-ablation",
) -> StarterProject:
    """Create a runnable starter experiment without overwriting user content.

    A destination may be absent, empty, an exact partial scaffold from an
    interrupted call, or a previously initialized project with a matching marker.
    Existing files in a marked project are preserved, including operator edits;
    only missing managed files are recreated.
    """

    experiment_name = _validate_name(name)
    destination = destination.expanduser()
    if destination.is_symlink():
        raise StarterError(f"starter destination cannot be a symlink: {destination}")

    # Keep the caller-visible spelling stable while using the canonical path for
    # every filesystem operation.  ``resolve()`` changes otherwise valid path
    # identities on common platforms (for example /var -> /private/var on macOS
    # and 8.3 aliases -> long names on Windows).  Returning paths rooted at that
    # canonical spelling makes them compare unequal to paths derived from the
    # destination the caller supplied.  ``absolute()`` removes dependence on the
    # current working directory without resolving aliases or symlinked parents.
    public_root = destination.absolute()
    root = destination.resolve()
    if root.exists() and not root.is_dir():
        raise StarterError(f"starter destination is not a directory: {root}")

    rendered = _rendered_files(experiment_name)
    marker = root / _MARKER
    managed = marker.exists() or marker.is_symlink()

    if root.exists():
        if managed:
            _load_marker(marker, expected_name=experiment_name)
        elif any(root.iterdir()):
            _verify_interrupted_scaffold(root, rendered)
    _preflight_targets(root, rendered, managed=managed)

    root.mkdir(parents=True, exist_ok=True)
    created_relative: list[PurePosixPath] = []
    preserved_relative: list[PurePosixPath] = []
    for relative, content in rendered.items():
        target = root.joinpath(*relative.parts)
        if target.exists():
            preserved_relative.append(relative)
            continue
        _write_new_file(target, content)
        created_relative.append(relative)

    if marker.exists():
        preserved_relative.append(PurePosixPath(_MARKER))
    else:
        _write_new_file(marker, _marker_bytes(experiment_name))
        created_relative.append(PurePosixPath(_MARKER))

    created = tuple(public_root.joinpath(*path.parts) for path in created_relative)
    preserved = tuple(public_root.joinpath(*path.parts) for path in preserved_relative)

    return StarterProject(
        root=public_root,
        config_path=public_root / "experiment.json",
        created_files=created,
        preserved_files=preserved,
    )
