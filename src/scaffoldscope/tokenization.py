"""Deterministic token estimates used for provider-independent experiments."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Char4TokenCounter:
    """A deliberately simple, logged approximation: four UTF-8 bytes per token."""

    name: str = "char4-v1"

    def text(self, value: str) -> int:
        return max(1, math.ceil(len(value.encode("utf-8")) / 4))

    def message(self, role: str, content: str) -> int:
        return 4 + self.text(role) + self.text(content)

    def messages(self, values: Iterable[Mapping[str, str]]) -> int:
        return 2 + sum(self.message(item["role"], item["content"]) for item in values)
