"""Packaged JSON Schema access for editors and automation."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from scaffoldscope.errors import ConfigError
from scaffoldscope.jsonutil import atomic_write_text


def config_schema_text() -> str:
    resource = files("scaffoldscope").joinpath("schemas").joinpath("experiment.schema.json")
    try:
        return resource.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        raise ConfigError("Packaged experiment JSON Schema is unavailable") from exc


def export_config_schema(output: Path) -> Path:
    destination = output.resolve()
    content = config_schema_text()
    if destination.exists():
        try:
            current = destination.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"Could not inspect schema destination {destination}: {exc}") from exc
        if current != content:
            raise ConfigError(f"Refusing to overwrite a different file: {destination}")
        return destination
    atomic_write_text(destination, content)
    return destination
