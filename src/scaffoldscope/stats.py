"""Stdlib-only paired estimates and task-cluster bootstrap intervals."""

from __future__ import annotations

import hashlib
import math
import statistics
from collections.abc import Iterable, Iterator, Sequence

RESAMPLING_ALGORITHM = "sha256-counter-v1"
_WORD_SPACE = 1 << 256


def _hash_words(*, seed: int, stream: str) -> Iterator[int]:
    counter = 0
    while True:
        material = f"{RESAMPLING_ALGORITHM}\0{stream}\0{seed}\0{counter}".encode()
        yield int.from_bytes(hashlib.sha256(material).digest(), "big")
        counter += 1


def _bounded_draws(limit: int, *, seed: int, stream: str) -> Iterator[int]:
    words = _hash_words(seed=seed, stream=stream)
    cutoff = _WORD_SPACE - (_WORD_SPACE % limit)
    while True:
        word = next(words)
        if word < cutoff:
            yield word % limit


def mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def percentile(values: Sequence[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def bootstrap_mean_interval(
    task_values: Sequence[float], *, samples: int, seed: int
) -> tuple[float | None, float | None]:
    if not task_values:
        return None, None
    size = len(task_values)
    draws = _bounded_draws(size, seed=seed, stream="bootstrap-mean-v1")
    estimates = [
        statistics.fmean(task_values[next(draws)] for _ in range(size)) for _ in range(samples)
    ]
    return percentile(estimates, 0.025), percentile(estimates, 0.975)


def empirical_mde(task_effects: Sequence[float], *, power: float = 0.8) -> float | None:
    if len(task_effects) < 10 or power <= 0 or power >= 1:
        return None
    standard_deviation = statistics.stdev(task_effects)
    if standard_deviation == 0:
        return None
    normal = statistics.NormalDist()
    multiplier = normal.inv_cdf(0.975) + normal.inv_cdf(power)
    return multiplier * standard_deviation / math.sqrt(len(task_effects))


def prospective_paired_mde(
    task_count: int, *, anticipated_discordance: float = 0.2, power: float = 0.8
) -> float | None:
    """Approximate paired-binary MDE under a declared discordance assumption."""

    if (
        task_count < 1
        or anticipated_discordance <= 0
        or anticipated_discordance > 1
        or power <= 0
        or power >= 1
    ):
        return None
    normal = statistics.NormalDist()
    multiplier = normal.inv_cdf(0.975) + normal.inv_cdf(power)
    return multiplier * math.sqrt(anticipated_discordance / task_count)


def paired_sign_flip_pvalue(
    task_effects: Sequence[float], *, draws: int = 100_000, seed: int = 20260815
) -> float | None:
    nonzero = [value for value in task_effects if value != 0]
    if not nonzero:
        return 1.0 if task_effects else None
    observed = abs(statistics.fmean(nonzero))
    extreme = 0
    if len(nonzero) <= 20:
        total = 1 << len(nonzero)
        for mask in range(total):
            estimate = statistics.fmean(
                value if mask & (1 << index) else -value for index, value in enumerate(nonzero)
            )
            extreme += int(abs(estimate) >= observed - 1e-12)
        return extreme / total
    words = _hash_words(seed=seed, stream="paired-sign-flip-v1")
    word = 0
    remaining_bits = 0
    for _ in range(draws):
        signed_values: list[float] = []
        for value in nonzero:
            if remaining_bits == 0:
                word = next(words)
                remaining_bits = 256
            signed_values.append(value if word & 1 else -value)
            word >>= 1
            remaining_bits -= 1
        estimate = statistics.fmean(signed_values)
        extreme += int(abs(estimate) >= observed - 1e-12)
    return (extreme + 1) / (draws + 1)


def finite(values: Iterable[float | int | None]) -> list[float]:
    result: list[float] = []
    for value in values:
        if value is None:
            continue
        numeric = float(value)
        if math.isfinite(numeric):
            result.append(numeric)
    return result
