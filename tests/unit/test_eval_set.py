"""Shape and coverage checks for the end-to-end evaluation set and published-value fixtures."""

import importlib.util
import json
import math
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = ROOT / "evaluations" / "questions.yaml"
FIXTURES = ROOT / "tests" / "fixtures" / "published"
YEARS = (2019, 2020)
GEOGRAPHIES = ("us_excluding_pr", "state:06")

TRAPS = {
    "wrong_denominator", "median_rollup", "combined_characteristic", "race_low_coverage",
    "year_2016_2018", "orange_county", "zip_code", "change_2019_2020",
    "census_count_vs_acs", "missing_data_total",
}
MULTI_TURN = {"change_place", "change_year", "change_race", "answer_clarification"}
KINDS = {"number", "refusal", "clarification"}

# Full-coverage measures of the plan's Task 5 table (all except mean_household_income_{race}).
FULL_COVERAGE_MEASURES = {
    "sex_ratio", "age_dependency_ratio", "child_dependency_ratio", "old_age_dependency_ratio",
    "mean_travel_time_to_work", "high_school_or_higher_pct", "bachelors_or_higher_pct",
    "poverty_rate", "family_poverty_rate", "mean_household_income", "mean_family_income",
    "per_capita_income", "mean_household_earnings", "mean_earnings", "mean_earnings_male",
    "mean_earnings_female", "unemployment_rate", "labor_force_participation_rate",
    "homeowner_vacancy_rate", "rental_vacancy_rate", "average_household_size",
    "average_household_size_owner", "average_household_size_renter", "homeownership_rate",
    "renters_paying_30_percent_or_more", "crowded_homes_pct", "uninsured_pct",
    "limited_english_households_pct", "no_internet_access_pct", "no_internet_subscription_pct",
}

# Tolerances fixed by the controller (counts exact, rates 0.2 pp, dollars 0.5%) and spec
# criterion 5 (minutes 0.5%, ratios and household size 0.01).
TOLERANCE_BY_UNIT = {
    "count": {"kind": "exact"},
    "percent": {"kind": "absolute", "amount": 0.2},
    "dollars": {"kind": "relative", "amount": 0.005},
    "minutes": {"kind": "relative", "amount": 0.005},
    "ratio": {"kind": "absolute", "amount": 0.01},
    "people_per_household": {"kind": "absolute", "amount": 0.01},
}
CENSUS_CODE = re.compile(r"\b(?:[BC]\d{5}[A-I]?(?:e\d+|_\d+E?)?|DP0\d|S\d{4}|P\d{7})\b")


def _cases():
    return yaml.safe_load(QUESTIONS.read_text())["cases"]


def _fixtures():
    entries = []
    for year in YEARS:
        path = FIXTURES / f"{year}.json"
        assert path.exists(), f"missing fixture {path}"
        entries.extend(json.loads(path.read_text()))
    return entries


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _numeric_expectations(case):
    expected = case["expected"]
    if expected["kind"] == "number":
        yield expected
        yield from expected.get("also", [])
    for step in case.get("intermediate", []):
        if step["kind"] == "number":
            yield step


# ---------------------------------------------------------------- evaluation set


def test_at_least_40_cases_with_unique_ids():
    ids = [case["id"] for case in _cases()]
    assert len(ids) >= 40
    assert len(ids) == len(set(ids))


def test_every_trap_and_multi_turn_case_present():
    tags = {tag for case in _cases() for tag in case["traps"]}
    assert TRAPS <= tags, TRAPS - tags
    assert MULTI_TURN <= tags, MULTI_TURN - tags
    assert tags <= TRAPS | MULTI_TURN, tags - (TRAPS | MULTI_TURN)


def test_multi_turn_cases_have_several_turns():
    for case in _cases():
        if set(case["traps"]) & MULTI_TURN:
            assert len(case["turns"]) >= 2, case["id"]


def test_case_shape():
    for case in _cases():
        assert set(case) <= {"id", "turns", "expected", "traps", "intermediate", "notes"}, case
        assert case["turns"] and all(isinstance(t, str) and t.strip() for t in case["turns"])
        expected = case["expected"]
        assert expected["kind"] in KINDS, case["id"]
        assert isinstance(expected["must_mention"], list) and expected["must_mention"], case["id"]
        assert isinstance(expected["must_not"], list), case["id"]
        if expected["kind"] != "number":
            assert "value" not in expected and "also" not in expected, case["id"]
        for step in case.get("intermediate", []):
            assert step["kind"] in {"clarification", "number"}, case["id"]
            assert step["must_mention"], case["id"]
        assert len(case.get("intermediate", [])) <= len(case["turns"]) - 1, case["id"]


def test_questions_are_plain_english_without_census_codes():
    for case in _cases():
        for turn in case["turns"]:
            assert not CENSUS_CODE.search(turn), (case["id"], turn)


def test_numeric_expectations_have_value_unit_and_tolerance():
    for case in _cases():
        for item in _numeric_expectations(case):
            assert _is_number(item["value"]), case["id"]
            assert item["unit"] in TOLERANCE_BY_UNIT, case["id"]
            assert item["tolerance"] == TOLERANCE_BY_UNIT[item["unit"]], case["id"]


def test_every_numeric_value_has_a_source():
    fixtures = {(e["measure"], e["geography"], e["year"]): e for e in _fixtures()}
    for case in _cases():
        for item in _numeric_expectations(case):
            sql, ref = item.get("reference_sql"), item.get("fixture")
            assert bool(sql) != bool(ref), f"{case['id']}: exactly one source required"
            if sql:
                assert re.match(r"\s*(SELECT|WITH)\b", sql, re.I), case["id"]
                assert not re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|DROP|ALTER)\b",
                                     sql, re.I), case["id"]
            else:
                key = (ref["measure"], ref["geography"], ref["year"])
                assert key in fixtures, case["id"]
                assert fixtures[key]["value"] == item["value"], case["id"]


def test_clarification_and_refusal_cases_present():
    kinds = [case["expected"]["kind"] for case in _cases()]
    assert kinds.count("clarification") >= 3
    assert kinds.count("refusal") >= 3
    assert kinds.count("number") >= 25


# ---------------------------------------------------------------- published fixtures


def test_fixture_entries_are_complete():
    for entry in _fixtures():
        assert {"measure", "geography", "year", "variable", "label", "value"} <= set(entry)
        if entry["value"] is None:
            assert entry["variable"] is None and entry["label"] is None, entry
            assert isinstance(entry.get("reason"), str) and entry["reason"].strip(), entry
        else:
            assert isinstance(entry["variable"], str) and entry["variable"].strip(), entry
            assert isinstance(entry["label"], str) and entry["label"].strip(), entry
            assert _is_number(entry["value"]) and entry["value"] >= 0, entry
            for part in entry.get("components", []):
                assert part["variable"] and part["label"] and _is_number(part["value"]), entry


def test_fixtures_cover_every_full_coverage_measure_geography_and_year():
    keys = [(e["measure"], e["geography"], e["year"]) for e in _fixtures()]
    assert len(keys) == len(set(keys))
    expected = {(m, g, y) for m in FULL_COVERAGE_MEASURES for g in GEOGRAPHIES for y in YEARS}
    assert set(keys) == expected


def test_fixture_year_matches_file():
    for year in YEARS:
        for entry in json.loads((FIXTURES / f"{year}.json").read_text()):
            assert entry["year"] == year


# ---------------------------------------------------------------- fetch script (offline)


def _script():
    spec = importlib.util.spec_from_file_location(
        "fetch_published_values", ROOT / "scripts" / "fetch_published_values.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VARIABLES = {
    "DP03_0063E": {"label": "Estimate!!INCOME AND BENEFITS (IN 2020 INFLATION-ADJUSTED "
                            "DOLLARS)!!Total households!!Mean household income (dollars)",
                   "group": "DP03"},
    "DP03_0063PE": {"label": "Percent!!INCOME AND BENEFITS (IN 2020 INFLATION-ADJUSTED "
                             "DOLLARS)!!Total households!!Mean household income (dollars)",
                    "group": "DP03"},
    "DP03_0063EA": {"label": "Annotation of Estimate!!INCOME AND BENEFITS", "group": "DP03"},
    "DP03PR_0063E": {"label": "Estimate!!INCOME AND BENEFITS (IN 2020 INFLATION-ADJUSTED "
                              "DOLLARS)!!Total households!!Mean household income (dollars)",
                     "group": "DP03PR"},
}


def test_find_variable_matches_label_within_group_and_year():
    module = _script()
    pattern = (r"Estimate!!INCOME AND BENEFITS \(IN {year} INFLATION-ADJUSTED DOLLARS\)"
               r"!!Total households!!Mean household income \(dollars\)")
    assert module.find_variable(VARIABLES, "DP03", pattern, 2020) == "DP03_0063E"
    with pytest.raises(LookupError):
        module.find_variable(VARIABLES, "DP03", pattern, 2019)


def test_find_variable_rejects_ambiguous_labels():
    module = _script()
    variables = dict(VARIABLES, DP03_0999E=VARIABLES["DP03_0063E"])
    with pytest.raises(LookupError):
        module.find_variable(variables, "DP03", r"Estimate!!.*Mean household income.*", 2020)


def test_parse_value_rejects_annotation_codes():
    module = _script()
    assert module.parse_value("63000") == 63000.0
    for bad in ["-666666666", "-888888888", None, "N"]:
        with pytest.raises(ValueError):
            module.parse_value(bad)


def test_script_specs_cover_every_full_coverage_measure():
    assert set(_script().measure_names()) == FULL_COVERAGE_MEASURES


# Rule D (spec section 10): every 2019-vs-2020 answer explains the period overlap, the redrawn
# block-group boundaries, and that the difference is not a measured change.
RULE_D_CONCEPTS = ("share 4 of 5 survey years", "block-group boundaries were redrawn",
                   "not a measured change")


def test_comparison_cases_require_rule_d_explanations():
    cases = [case for case in _cases() if "change_2019_2020" in case["traps"]]
    assert len(cases) >= 3
    for case in cases:
        for concept in RULE_D_CONCEPTS:
            assert concept in case["expected"]["must_mention"], (case["id"], concept)
