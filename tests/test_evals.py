"""Offline tests for the accuracy checker in evals/run.py (no API calls)."""

from evals.cases import CASES, says_zero
from evals.run import compare


def test_matches_reference_column_by_value_ignoring_names_and_extra_columns():
    reference = [(829,)]
    assert compare(reference, ["tickets_sold", "revenue"], [(829, 242452.0)], ordered=False) == []


def test_numbers_match_within_a_cent():
    assert compare([(310.4,)], ["avg"], [(310.404,)], ordered=False) == []
    assert compare([(310.4,)], ["avg"], [(310.42,)], ordered=False) != []


def test_ordered_reference_must_be_the_first_rows_in_order():
    reference = [(3,), (2,)]
    assert compare(reference, ["x"], [(3,), (2,), (1,)], ordered=True) == []
    assert compare(reference, ["x"], [(2,), (3,), (1,)], ordered=True) != []
    assert compare(reference, ["x"], [(3,)], ordered=True) != []


def test_unordered_reference_needs_the_same_rows_in_any_order():
    reference = [(1135,), (939,)]
    assert compare(reference, ["x"], [(939,), (1135,)], ordered=False) == []
    assert compare(reference, ["x"], [(2074,)], ordered=False) != []   # merged into one row


def test_empty_reference_is_reported():
    assert compare([], ["x"], [(1,)], ordered=False) == ["reference query returned no rows"]


def test_zero_check_flags_zero_figures():
    assert says_zero("Hamilton sold 0 tickets.")
    assert says_zero("Revenue in 2028 was $0.")
    assert says_zero("It sold zero tickets in 2028.")


def test_zero_check_allows_honest_empty_wording():
    # Wording the live model actually produced for empty results.
    assert not says_zero("No sales data was found for Hamilton.")
    assert not says_zero("There's no revenue for 2028 because it hasn't happened yet.")
    assert not says_zero("This means the show doesn't appear in the sales records at all, "
                         "not that it sold zero tickets.")


def test_case_ids_are_unique():
    ids = [c.id for c in CASES]
    assert len(ids) == len(set(ids))
