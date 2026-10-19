from pathlib import Path

PROMPTS = Path(__file__).resolve().parents[2] / "census_compass" / "prompts"
TOOLS = ["resolve_year", "resolve_population_group", "resolve_geography",
         "search_schema", "get_schema", "execute_query"]


def test_domain_rules_size_and_phrases():
    text = (PROMPTS / "domain_rules.md").read_text()
    assert len(text) / 4 <= 1500
    for phrase in ["US excluding Puerto Rico", "not adjusted for inflation", "90%",
                   "CENSUS_BLOCK_GROUP", "never aggregated"]:
        assert phrase in text


def test_instructions_name_all_tools():
    text = (PROMPTS / "agent_instructions.md").read_text()
    for tool in TOOLS:
        assert tool in text


OUTCOMES = ["answered", "clarification_needed", "not_available", "unsupported",
            "query_rejected", "timed_out", "database_failure", "model_failure", "truncated"]
FIELDS = ["block_groups", "missing_inputs", "unknown_denominator", "coverage", "missing_moe"]


def _read(name):
    return (PROMPTS / name).read_text()


def test_instructions_outcomes_and_fields():
    text = _read("agent_instructions.md")
    for item in OUTCOMES + FIELDS:
        assert item in text


def test_instructions_resolver_behaviours():
    text = _read("agent_instructions.md")
    for phrase in ["not a current figure", "that year only", "offer them", "Run no query"]:
        assert phrase in text


def test_domain_rules_joins_table_and_completeness():
    text = _read("domain_rules.md")
    assert '"A"."CENSUS_BLOCK_GROUP" = "B"."CENSUS_BLOCK_GROUP"' in text
    assert 'LEFT("A"."CENSUS_BLOCK_GROUP", 5)' in text
    assert "{table}" in text and "2020_CBG_B01" in text
    for phrase in ["unknown denominators", "missing block groups", "show no ±", "coverage below 90%"]:
        assert phrase in text
    assert len(text) / 4 <= 1500


def test_instructions_role_line():
    assert "You are Census Compass" in _read("agent_instructions.md")


def test_instructions_definition_question_needs_no_query():
    text = " ".join(_read("agent_instructions.md").split())
    assert "definition" in text
    assert "Do not run a query merely to answer a definition question" in text


def test_instructions_verify_step():
    text = _read("agent_instructions.md")
    assert "Verify before answering" in text
    assert "Missing or unavailable values are not zero" in text


def test_instructions_field_code_exception():
    text = " ".join(_read("agent_instructions.md").split())
    assert "field codes" in text
    assert "B01001e3" in text
    assert "retrieved definitions" in text


def test_system_prompt_under_3000_tokens():
    total = len(_read("agent_instructions.md")) + len(_read("domain_rules.md"))
    assert total / 4 < 3000
