"""Offline tests for the accuracy checker in evals/run.py (no API calls)."""

from evals.cases import CASES, asserts, says_zero, self_corrects
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


def test_forbidden_wording_is_matched_outside_denials():
    pattern = next(c for c in CASES if c.id == "asking_vs_paid").forbid_answer
    # The misleading answer from the stress test, and a variant.
    assert asserts(pattern, "In every category, buyers paid more on average than the "
                            "average asking price.")
    assert asserts(pattern, "Buyers paid above the asking price.")
    # Correct explanations are allowed.
    assert not asserts(pattern, "Pricier listings sold more tickets, so the average paid "
                                "is higher than the average asking price.")
    assert not asserts(pattern, "Every sale was at its asking price: buyers never paid "
                                "more than asking.")


def test_a_zero_next_to_unrelated_no_is_still_flagged():
    assert says_zero("No data, so revenue was $0.")


def test_self_correction_is_flagged():
    # The live answer that prompted this check.
    assert self_corrects("Barclays Center, with $1,589,735.00 from 4,812... I mean 4,810 tickets.")
    assert self_corrects("Revenue was $5M. Sorry, it was $6M.")
    assert self_corrects("Wait, that includes January.")


def test_ordinary_answers_are_not_flagged_as_self_corrections():
    assert not self_corrects("Pop led with $62,434,243, followed by Plays at $25,538,940.")
    assert not self_corrects("Every sale happens at exactly the listing's asking price.")
    assert not self_corrects("This means the show doesn't appear in the sales records at all.")


def test_asking_price_gap_with_the_right_explanation_is_allowed():
    pattern = next(c for c in CASES if c.id == "asking_vs_paid").forbid_answer
    # Live answer at medium effort: states the gap and gives the correct reason.
    assert not asserts(pattern, "In every category, buyers paid more per ticket on average "
                                "than the average asking price, meaning pricier listings "
                                "sold better.")
