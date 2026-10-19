"""Application settings loaded from environment variables and an optional .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    agent_model: str = "anthropic/claude-sonnet-5-5"
    # Provisional default; the final embedding model is chosen in a later task.
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    lancedb_path: Path = Path("data/index")
    semantic_dir: Path = Path("definitions/generated")
    source_dir: Path = Path("definitions/source")
    v1_tables_dir: Path = Path("data/tables")
    request_budget_seconds: float = 60
    min_statement_timeout_seconds: int = 5
    max_result_rows: int = 1000
    max_sql_retries: int = 2
    topic_token_limit: int = 12000
    coverage_threshold: float = 0.9
    guardrail_model: str = "anthropic/claude-haiku-4-5-20251001"
    guardrail_timeout_seconds: float = 5
    max_input_chars: int = 2000
    guardrail_context_turns: int = 3
    glossary_path: Path = Path("definitions/source/census_glossary.yaml")
    snowflake_account: str = "DNPKSVD-ARC86250"
    snowflake_user: str = "SAHILGARG5794"
    snowflake_role: str = "CENSUS_READER"
    snowflake_warehouse: str = "COMPUTE_WH"
    snowflake_database: str = "US_OPEN_CENSUS_DATA_NEIGHBORHOOD_INSIGHTS_FREE_DATASET"
    snowflake_schema: str = "PUBLIC"


@lru_cache
def get_settings() -> Settings:
    return Settings()
