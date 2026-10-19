from pathlib import Path

PROMPTS = Path(__file__).resolve().parents[2] / "census_compass" / "prompts"
TOOLS = ["resolve_year", "resolve_population_group", "resolve_geography",
         "search_schema", "get_schema", "execute_query"]


def test_domain_rules_size_and_phrases():
    text = (PROMPTS / "domain_rules.md").read_text()
    assert len(text) / 4 <= 1500
    for phrase in ["US including Puerto Rico", "not adjusted for inflation", "90%",
                   "CENSUS_BLOCK_GROUP", "never aggregated"]:
        assert phrase in text


def test_instructions_name_all_tools():
    text = (PROMPTS / "agent_instructions.md").read_text()
    for tool in TOOLS:
        assert tool in text
