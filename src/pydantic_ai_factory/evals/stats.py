"""The statistics behind the scorecard, as pure functions. No statistics library is used.

The case is the unit of analysis: repeats of one case are not independent. So a rate is
each case's own rate, averaged across cases, with an interval from the spread between
cases, and a comparison is the difference case by case. A Wilson interval is used only
for a plain count of independent runs.

The intervals are 95 per cent intervals. :data:`T_VALUES` holds the two-sided t value for
1 to 30 degrees of freedom; a list of n cases uses the value for n minus 1. With one case,
or more than 31, there is no interval and both bounds are ``None``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Z_95 = 1.96
"""The two-sided 95 per cent value of the normal distribution, for :func:`wilson`."""

T_VALUES: dict[int, float] = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
    11: 2.201,
    12: 2.179,
    13: 2.160,
    14: 2.145,
    15: 2.131,
    16: 2.120,
    17: 2.110,
    18: 2.101,
    19: 2.093,
    20: 2.086,
    21: 2.080,
    22: 2.074,
    23: 2.069,
    24: 2.064,
    25: 2.060,
    26: 2.056,
    27: 2.052,
    28: 2.048,
    29: 2.045,
    30: 2.042,
}
"""The two-sided 95 per cent t value for each number of degrees of freedom, 1 to 30."""

Interval = tuple[float, float | None, float | None]
"""A mean and its lower and upper bounds; the bounds are ``None`` when there is no interval."""


class NoData(ValueError):
    """A statistic was asked for with nothing to compute it from."""


def wilson(passes: int, n: int) -> tuple[float, float]:
    """The Wilson 95 per cent interval for ``passes`` out of ``n`` independent runs.

    Use it only for a plain count of independent runs, never for repeats of one case.
    Raises :class:`NoData` when there are no runs.
    """
    if n < 1:
        raise NoData("a Wilson interval needs at least one run")
    if not 0 <= passes <= n:
        raise ValueError(f"passes must be between 0 and {n}, not {passes}")
    rate = passes / n
    z2 = Z_95 * Z_95
    centre = rate + z2 / (2 * n)
    spread = Z_95 * math.sqrt(rate * (1 - rate) / n + z2 / (4 * n * n))
    scale = 1 + z2 / n
    return max(0.0, (centre - spread) / scale), min(1.0, (centre + spread) / scale)


def clustered_rate(case_rates: Sequence[float]) -> Interval:
    """The mean of the per-case rates, with an interval from the spread between cases.

    The interval is the mean plus and minus t times the standard error between cases,
    kept within 0 and 1. Raises :class:`NoData` when there are no cases.
    """
    mean, low, high = _mean_interval(case_rates)
    if low is None or high is None:
        return mean, None, None
    return mean, max(0.0, low), min(1.0, high)


def paired_difference(a_rates: Sequence[float], b_rates: Sequence[float]) -> Interval:
    """The mean of ``a`` minus ``b``, case by case, with an interval from those differences.

    The two lists hold one rate for each case, in the same case order. The interval is
    kept within minus 1 and 1. Raises :class:`NoData` when there are no cases.
    """
    if len(a_rates) != len(b_rates):
        raise ValueError(
            f"the two arms need a rate for the same cases, not {len(a_rates)} and {len(b_rates)}"
        )
    mean, low, high = _mean_interval([a - b for a, b in zip(a_rates, b_rates, strict=True)])
    if low is None or high is None:
        return mean, None, None
    return mean, max(-1.0, low), min(1.0, high)


def pass_k(passes: int, repeats: int, k: int) -> float | None:
    """The chance that ``k`` attempts at a case, drawn from its repeats, all succeed.

    It is the number of ways to choose ``k`` passing repeats over the number of ways to
    choose ``k`` repeats. With fewer repeats than ``k`` it is ``None``.
    """
    if k < 1:
        raise ValueError(f"k is at least 1, not {k}")
    if not 0 <= passes <= repeats:
        raise ValueError(f"passes must be between 0 and {repeats}, not {passes}")
    if repeats < k:
        return None
    return math.comb(passes, k) / math.comb(repeats, k)


def _mean_interval(values: Sequence[float]) -> Interval:
    n = len(values)
    if n == 0:
        raise NoData("a mean needs at least one case")
    mean = sum(values) / n
    t = T_VALUES.get(n - 1)
    if t is None:
        return mean, None, None
    spread = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
    half = t * spread / math.sqrt(n)
    return mean, mean - half, mean + half
