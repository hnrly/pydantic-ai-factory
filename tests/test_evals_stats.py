"""The statistics behind the scorecard, three of them checked against the proposal's examples."""

from __future__ import annotations

import math

import pytest

from pydantic_ai_factory.evals.stats import (
    T_VALUES,
    NoData,
    clustered_rate,
    paired_difference,
    pass_k,
    wilson,
)


def test_eighteen_passes_in_thirty_give_a_wilson_interval_of_0_42_to_0_75() -> None:
    low, high = wilson(18, 30)

    assert (round(low, 2), round(high, 2)) == (0.42, 0.75)


def test_a_case_passing_four_of_five_runs_scores_0_8_for_one_attempt_and_0_4_for_three() -> None:
    assert pass_k(4, 5, 1) == pytest.approx(0.8)
    assert pass_k(4, 5, 3) == pytest.approx(0.4)


def test_thirty_pairs_with_a_mean_difference_of_0_20_and_a_spread_of_0_35_give_0_07_to_0_33() -> (
    None
):
    # Half the differences sit above 0.20 and half below, by the amount that makes their
    # standard deviation, with n - 1, exactly 0.35.
    step = 0.35 * math.sqrt(29 / 30)
    differences = [0.20 + step] * 15 + [0.20 - step] * 15
    b_rates = [0.3] * 30
    a_rates = [b + d for b, d in zip(b_rates, differences, strict=True)]

    mean, low, high = paired_difference(a_rates, b_rates)

    assert mean == pytest.approx(0.20)
    assert low is not None and high is not None
    assert (round(low, 2), round(high, 2)) == (0.07, 0.33)


def test_clustered_rate_over_four_rates_gives_a_mean_of_0_5_and_bounds_worked_by_hand() -> None:
    # By hand: the squares about 0.5 sum to 0.2, so the spread is sqrt(0.2 / 3) = 0.2582,
    # the standard error 0.2582 / 2 = 0.1291, and t for three degrees of freedom is 3.182,
    # so the half-width is 0.4108.
    mean, low, high = clustered_rate([0.2, 0.4, 0.6, 0.8])

    assert mean == pytest.approx(0.5)
    assert low == pytest.approx(0.5 - 0.4108, abs=1e-4)
    assert high == pytest.approx(0.5 + 0.4108, abs=1e-4)


def test_one_case_gives_a_mean_and_no_interval() -> None:
    assert clustered_rate([0.75]) == (0.75, None, None)
    assert paired_difference([1.0], [0.5]) == (0.5, None, None)


def test_more_cases_than_the_t_table_holds_give_a_mean_and_no_interval() -> None:
    assert clustered_rate([0.5] * 31)[1] is not None
    assert clustered_rate([0.5] * 32) == (0.5, None, None)


def test_the_t_table_holds_one_to_thirty_degrees_of_freedom() -> None:
    assert sorted(T_VALUES) == list(range(1, 31))
    assert T_VALUES[29] == pytest.approx(2.045)


def test_a_clustered_rate_is_kept_between_0_and_1() -> None:
    _, low, high = clustered_rate([0.0, 1.0])

    assert (low, high) == (0.0, 1.0)


def test_zero_runs_or_zero_cases_raise_no_data() -> None:
    with pytest.raises(NoData):
        wilson(0, 0)
    with pytest.raises(NoData):
        clustered_rate([])
    with pytest.raises(NoData):
        paired_difference([], [])


def test_all_passes_give_a_wilson_interval_ending_at_1_and_a_pass_k_of_1() -> None:
    low, high = wilson(30, 30)

    assert high == 1.0
    assert round(low, 2) == 0.89
    assert pass_k(5, 5, 3) == 1.0
    assert clustered_rate([1.0, 1.0, 1.0]) == (1.0, 1.0, 1.0)


def test_fewer_repeats_than_k_give_no_pass_k() -> None:
    assert pass_k(2, 2, 3) is None
    assert pass_k(0, 0, 1) is None


def test_two_arms_with_rates_for_different_numbers_of_cases_cannot_be_paired() -> None:
    with pytest.raises(ValueError, match="same cases"):
        paired_difference([0.5, 0.5], [0.5])
