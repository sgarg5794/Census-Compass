from census_compass.config import Settings


def test_defaults_match_spec(monkeypatch):
    monkeypatch.delenv("MAX_RESULT_ROWS", raising=False)
    s = Settings(_env_file=None, agent_model="x", embedding_model="y")
    assert (s.request_budget_seconds, s.max_result_rows, s.max_sql_retries) == (60, 1000, 2)
    assert (s.topic_token_limit, s.coverage_threshold, s.min_statement_timeout_seconds) == (
        12000,
        0.9,
        5,
    )
    assert s.snowflake_role == "CENSUS_READER"


def test_env_example_does_not_override_defaults(tmp_path):
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / ".env.example"
    tmp = tmp_path / ".env"
    tmp.write_text(src.read_text())
    s = Settings(_env_file=tmp)
    assert s.embedding_model == "BAAI/bge-small-en-v1.5"
    assert s.agent_model == "anthropic/claude-sonnet-5-5"
