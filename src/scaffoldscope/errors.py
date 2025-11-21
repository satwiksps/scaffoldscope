"""Typed failures used at CLI and trial boundaries."""

from __future__ import annotations


class ScaffoldScopeError(Exception):
    """Base class for expected, user-facing failures."""


class ConfigError(ScaffoldScopeError):
    """The experiment or task configuration is invalid."""


class ContextOverflowError(ScaffoldScopeError):
    """The active context cannot fit inside the configured input budget."""


class ModelError(ScaffoldScopeError):
    """A model provider failed or returned an unusable response."""


class ProtocolError(ScaffoldScopeError):
    """The model response did not follow the action protocol."""


class SandboxError(ScaffoldScopeError):
    """A requested workspace operation was rejected or failed."""
