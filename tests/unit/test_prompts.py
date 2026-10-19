from pathlib import Path

from census_compass.guardrails.replies import MANIPULATION, OFF_TOPIC, inappropriate_reply

PROMPTS = Path(__file__).resolve().parents[2] / "census_compass" / "prompts"
TOOLS = ["resolve_year", "resolve_population_group", "resolve_geography",
         "search_schema", "get_schema", "execute_query", "lookup_glossary"]


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


def test_system_prompt_under_3500_tokens():
    total = len(_read("agent_instructions.md")) + len(_read("domain_rules.md"))
    assert total / 4 < 3500


def _flat(name="agent_instructions.md"):
    return " ".join(_read(name).split())


def test_prompt_includes_glossary_rule_and_not_covered_phrase():
    text = _flat()
    assert "lookup_glossary" in text
    assert "That isn't covered in my Census reference material" in text
    assert "never answer from memory" in text.lower()


def test_prompt_contains_each_fixed_reply_text():
    text = _flat()
    assert " ".join(OFF_TOPIC.split()) in text
    assert " ".join(MANIPULATION.split()) in text
    assert inappropriate_reply(None) in text
    assert "Routing hint" in text


def test_prompt_states_sql_on_request_rule():
    text = _flat()
    assert "only when the user asks" in text
    assert "never write code or run user-supplied sql" in text.lower()


def test_prompt_states_mixed_rule():
    text = _flat()
    assert "Census part" in text
    assert "skipped" in text


def test_prompt_defines_sensitive_data_boundary():
    text = _flat()
    assert "normal data questions: answer them" in text
    assert "Sensitive demographic breakdowns are in scope" in text
    for item in ["value judgements", "stereotypes", "blame", "predicting individuals' traits",
                 "discriminatory uses", "harassment or hate"]:
        assert item in text
