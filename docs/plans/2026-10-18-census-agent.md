# Census Compass Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Phase 1 streams are independent and may run in parallel (superpowers:dispatching-parallel-agents), one git worktree per stream.

**Goal:** Build the Census Compass agent: generated topic semantic files, search index, six tools with mandatory query safety, domain rules, and an ADK agent that meets the spec's acceptance criteria.

**Architecture:** An offline builder converts the v1 per-table catalog (`data/tables/*.yaml`) plus a curated topic mapping and hand-written measure templates into LLM-facing topic files, builder metadata, an allowed-table list and a LanceDB hybrid index — in one command. At runtime an ADK `LlmAgent` (LiteLLM → Claude Sonnet) calls three resolvers, `search_schema`, `get_schema` and `execute_query`; `execute_query` validates SQL with sqlglot (fail-closed), adds completeness companions, and runs on Snowflake as `CENSUS_READER` within one 60-second request budget.

**Tech Stack:** Python 3.12, google-adk 2.12, litellm 1.106, sqlglot 30.23, lancedb 0.40 (native FTS + RRFReranker), sentence-transformers (new), snowflake-connector-python 5.0, pydantic 2, pytest 9.

**Spec:** `docs/specs/2026-10-18-schema-selection-design.md` (approved). Topic mapping: `docs/specs/2026-10-18-topics-draft.yaml`.

## Global Constraints

- Database `US_OPEN_CENSUS_DATA_NEIGHBORHOOD_INSIGHTS_FREE_DATASET`, schema `PUBLIC`, role `CENSUS_READER`, `USE SECONDARY ROLES NONE` on every connection, warehouse `COMPUTE_WH`. Never modify Snowflake objects, grants or settings.
- Credentials only via the existing macOS Keychain service `census-compass-snowflake:DNPKSVD-ARC86250`; never print, copy or store them elsewhere.
- Member `sql` is always qualified with the real physical table name (`"2020_CBG_B25"."B25064e1"`); no aliases in LLM-facing files.
- LLM-facing files contain no provenance, verification, coverage or missing-count fields.
- Excluded tables: `2019_CBG_PATTERNS`, `2019_RENT_PERCENTAGE_HOUSEHOLD_INCOME`, `2019_TOTAL_RENTAL_GEO`, `2019_CBG_B99`, `2020_CBG_B99`; geometry tables are never queried.
- Starting values: 60-second request budget; statement timeout = remaining budget, minimum 5 s; 2 SQL retries; 1,000 result rows; 12,000-token topic-size warning threshold (estimate: characters ÷ 4; warning only — reviewed topics are never split for the MVP); coverage reporting threshold 0.90.
- Embedding model fixed for the project once chosen (Task 15); same model for index and queries.
- National = "US including Puerto Rico"; no exclusion option.
- Generated files under `definitions/generated/` are never edited by hand.
- Worktree test command (no per-worktree venv): `PYTHONPATH=. /Users/sahilgarg/Desktop/Census-Compass/.venv/bin/python -m pytest …` run from the worktree root, so the worktree's code shadows the main checkout's editable install.
- Phase 1 streams must not modify Phase 0 files (`pyproject.toml`, `census_compass/config.py`, `census_compass/contracts.py`, `census_compass/semantic/race_groups.py`, `definitions/schema/*.json`). If a contract must change, stop and report.
- Lint: `ruff check .` clean before each commit.

## Review Focus

1. **Topic file over 12k tokens** (likely `rent_and_rent_burden`, `commuting`): the build must succeed and print a warning naming the file and its estimated size; the file is written complete, never truncated. Test in Task 6.
2. **Question with no year and a race group that has no race-specific measure** (e.g. "poverty rate of Asian people"): search must still return the general poverty topic so the agent can apply rule C. Test in Task 14.
3. **LLM SQL that ranks counties by a hand-written rate** (CTE + `RANK()` + `WHERE rank <= 10`): must pass validation and keep per-row completeness. Test in Task 11.
4. **Block group with population but no published value** (238 such rows for per capita income): ratio returns "not available" for that block group, not 0. Test in Task 5 fixtures.
5. **County name typed with different case/punctuation** ("cook county", "St. Louis city", "Doña Ana"): resolves to the right county or `ambiguous`, never `not_found` for an existing county. Test in Task 9.

---

## Phase 0 — Foundation (sequential, main checkout)

### Task 0: Baseline commit and branch

Requires the user's go-ahead on the file list (shown to them first).

**Files:** none created.

- [ ] **Step 1:** `git switch -c feature/census-agent`
- [ ] **Step 2:** `git status --porcelain` and `git check-ignore -v .secrets .env data/metadata .venv` — confirm secrets, raw extraction and venv are ignored; show the file list to the user.
- [ ] **Step 3:** after approval: `git add -A && git commit -m "chore: baseline scaffold, v1 catalog and approved design spec"`
- [ ] **Step 4:** `git ls-files | grep -E "secrets|\.env$|\.p8$"` → Expected: no output.

### Task 1: Dependencies and settings

**Files:**
- Modify: `pyproject.toml` (add `sentence-transformers` to dependencies)
- Modify: `census_compass/config.py`
- Modify: `.env.example` (`AGENT_MODEL=anthropic/claude-sonnet-5-5`, `EMBEDDING_PROVIDER=sentence-transformers`, `REQUEST_BUDGET_SECONDS=60`, `MAX_RESULT_ROWS=1000`, `MAX_SQL_RETRIES=2`, `TOPIC_TOKEN_LIMIT=12000`, `COVERAGE_THRESHOLD=0.9`, `SEMANTIC_DIR=definitions/generated`)
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Produces: `census_compass.config.Settings` (pydantic-settings, env file `.env`) with fields `agent_model: str`, `embedding_model: str`, `lancedb_path: Path = Path("data/index")`, `semantic_dir: Path = Path("definitions/generated")`, `source_dir: Path = Path("definitions/source")`, `v1_tables_dir: Path = Path("data/tables")`, `request_budget_seconds: float = 60`, `min_statement_timeout_seconds: int = 5`, `max_result_rows: int = 1000`, `max_sql_retries: int = 2`, `topic_token_limit: int = 12000`, `coverage_threshold: float = 0.9`, `snowflake_database`, `snowflake_schema`, `snowflake_role`, `snowflake_warehouse`, `snowflake_account`, `snowflake_user`; `get_settings() -> Settings` (cached).

- [ ] **Step 1: Write the failing test**
```python
def test_defaults_match_spec(monkeypatch):
    monkeypatch.delenv("MAX_RESULT_ROWS", raising=False)
    s = Settings(_env_file=None, agent_model="x", embedding_model="y")
    assert (s.request_budget_seconds, s.max_result_rows, s.max_sql_retries) == (60, 1000, 2)
    assert (s.topic_token_limit, s.coverage_threshold, s.min_statement_timeout_seconds) == (12000, 0.9, 5)
    assert s.snowflake_role == "CENSUS_READER"
```
- [ ] **Step 2:** `.venv/bin/python -m pytest tests/unit/test_config.py -v` → FAIL (no `Settings`).
- [ ] **Step 3:** Implement `Settings`/`get_settings`; `.venv/bin/python -m pip install sentence-transformers` and `pip freeze > requirements.lock.txt`.
- [ ] **Step 4:** Run test → PASS; `.venv/bin/python -c "import sentence_transformers"` → no error.
- [ ] **Step 5:** Commit `feat: settings and embedding dependency`.

### Task 2: Shared contracts, race groups and file schemas

**Files:**
- Create: `census_compass/contracts.py`
- Create: `census_compass/semantic/race_groups.py`
- Create: `definitions/schema/topic_file.schema.json`, `definitions/schema/builder_metadata.schema.json`
- Test: `tests/unit/test_contracts.py`

**Interfaces (Produces — every later task uses these exact names):**
```python
# census_compass/contracts.py  (pydantic BaseModel, extra="forbid")
class Dimension:  name: str; title: str; description: str; sql: str
                  type: Literal["string", "number"]; primary_key: bool = False
                  format: str | None = None; meta: dict[str, str] = {}
class Measure:    name: str; title: str; description: str; sql: str
                  type: Literal["sum", "number"]; format: str; meta: dict[str, str] = {}
class Join:       name: str; relationship: Literal["one_to_one", "many_to_one"]; sql: str
class TopicFile:  name: str; title: str; description: str; sql_table: str
                  dimensions: list[Dimension]; measures: list[Measure]; joins: list[Join] = []
class MemberMetadata: file: str; member: str; kind: Literal["dimension", "measure"]
                  table: str; source_column: str | None
                  aggregation: Literal["sum", "none", "formula"]
                  definition_source: Literal["dictionary", "census_api", "hand_written", "project"]
                  subject_definitions_page: str | None = None
                  normalized_sql: str | None = None          # hand-written only
                  numerator_columns: list[str] = []          # "TABLE.COLUMN"
                  denominator_columns: list[str] = []
                  coverage_sql: str | None = None
                  population_group: str | None = None
class BuilderMetadata: year: int; members: list[MemberMetadata]
                  table_columns: dict[str, list[str]]        # physical table -> columns
                  fips_table: str                            # e.g. "2020_METADATA_CBG_FIPS_CODES"
class AllowedTables: tables: dict[int, list[str]]            # year -> physical tables

class Period:     year: Literal[2019, 2020]; label: str      # "2016–2020"
                  sources: list[Literal["acs", "census_count"]]
class YearResolution: status: Literal["ok", "ambiguous", "unavailable"]
                  periods: list[Period] = []; comparison: bool = False; message: str = ""
class GeographyResolution: status: Literal["ok", "ambiguous", "unsupported", "not_found", "no_data"]
                  year: int; name: str | None = None
                  level: Literal["nation", "state", "county", "block_group"] | None = None
                  sql_filter: str | None = None; block_groups: int | None = None
                  candidates: list[str] = []; message: str = ""
class PopulationGroupResolution: status: Literal["ok", "ambiguous", "not_found"]
                  code: str | None = None; label: str | None = None
                  candidates: list[str] = []; message: str = ""
class MemberHit:  name: str; title: str
class SearchHit:  name: str; title: str; description: str; matched_members: list[MemberHit]
class Completeness: row: int; column: str; block_groups: int
                  missing_inputs: dict[str, int] = {}; unknown_denominator: int | None = None
                  coverage: float | None = None; missing_moe: int | None = None
class Outcome(StrEnum): ANSWERED, CLARIFICATION_NEEDED, NOT_AVAILABLE, UNSUPPORTED,
                  QUERY_REJECTED, TIMED_OUT, DATABASE_FAILURE, MODEL_FAILURE, TRUNCATED
class QueryResult: outcome: Outcome; rows: list[dict] = []; completeness: list[Completeness] = []
                  truncated: bool = False; message: str = ""; query_id: str | None = None
class QueryRejected(Exception): reason: str; suggestions: list[str]
class CountyRecord: state_abbr: str; state_fips: str; county_fips: str; county: str; block_groups: int

def load_builder_metadata(semantic_dir: Path, year: int) -> BuilderMetadata   # {dir}/{year}/builder_metadata.json
def load_allowed_tables(semantic_dir: Path) -> AllowedTables                 # {dir}/allowed_tables.json
def load_topic_files(semantic_dir: Path, year: int) -> list[TopicFile]        # {dir}/{year}/*.yaml
def load_geography(source_dir: Path, year: int) -> list[CountyRecord]         # {dir}/geography/{year}.json
```
These loaders live in Phase 0 so Streams B, C and D never import from Stream A.
```python
# census_compass/semantic/race_groups.py
@dataclass(frozen=True)
class RaceGroup: code: str; slug: str; label: str; synonyms: tuple[str, ...]
RACE_GROUPS: tuple[RaceGroup, ...]   # A..I exactly as spec §6.4; slugs:
# white_alone, black_alone, american_indian_alaska_native_alone, asian_alone,
# pacific_islander_alone, some_other_race_alone, two_or_more_races,
# white_alone_not_hispanic, hispanic_or_latino
def race_group(code: str) -> RaceGroup
```

- [ ] **Step 1: Write the failing tests**
```python
def test_race_groups_match_spec():
    assert [g.code for g in RACE_GROUPS] == list("ABCDEFGHI")
    assert race_group("E").label == "Native Hawaiian and Other Pacific Islander alone"
    assert "Latinx" in race_group("I").synonyms

def test_topic_file_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        TopicFile(name="a", title="t", description="d", sql_table="s",
                  dimensions=[], measures=[], coverage_sql="x")

def test_json_schema_matches_models():
    schema = json.loads(Path("definitions/schema/topic_file.schema.json").read_text())
    assert schema == TopicFile.model_json_schema()

def test_loaders_round_trip(tmp_path):
    (tmp_path / "geography").mkdir()
    (tmp_path / "geography" / "2020.json").write_text(json.dumps([{"state_abbr": "IL",
        "state_fips": "17", "county_fips": "031", "county": "Cook County", "block_groups": 4002}]))
    assert load_geography(tmp_path, 2020)[0].county_fips == "031"   # leading zeros kept as text
```
- [ ] **Step 2:** run `tests/unit/test_contracts.py` → FAIL.
- [ ] **Step 3:** implement models; write both JSON schema files from `model_json_schema()`.
- [ ] **Step 4:** run → PASS.
- [ ] **Step 5:** Commit `feat: shared contracts, race groups and file schemas`. **End of Phase 0 — streams branch from this commit.**

---

## Phase 1 — Parallel streams (one worktree per stream; streams A–E are independent)

### Stream A — Builder (Tasks 3–6)

### Task 3: Source inputs (topics, Census API labels, geography lookups)

**Files:**
- Create: `definitions/source/topics.yaml` (copy of the approved draft, unchanged content)
- Create: `scripts/fetch_census_api.py` → writes `definitions/source/census_api/{year}_{group}.json` for the 11 E groups (B01002E, B11001E, B11002E, B14007E, B19025E, B19301E, B19313E, B25003E, B28009E, C15010E, C24010E), 2019 and 2020
- Create: `scripts/extract_geography.py` → writes `definitions/source/geography/{year}.json` via the existing helper's `connect()`; content: list of `{state_abbr, state_fips, county_fips, county, block_groups}` (block_groups = demographic block groups with that 5-digit prefix, 0 for lookup counties without rows)
- Create: `census_compass/semantic/sources.py`
- Test: `tests/unit/test_sources.py`

**Interfaces:**
- Produces: `load_topics(path) -> list[TopicSpec]` where `TopicSpec(name, title, table, groups: list[str], internal: bool)`; `load_census_api_labels(dir, year) -> dict[str, str]` (column `B01002Ee2` → label text without `Estimate!!`); the geography JSON files in the `CountyRecord` format read by `contracts.load_geography` (Task 2).

- [ ] **Step 1: Write failing tests**
```python
def test_topics_parse_and_exclude_tables():
    topics = load_topics(Path("definitions/source/topics.yaml"))
    assert len(topics) == 49 and sum(t.internal for t in topics) == 2
    assert not any(t.table.endswith(("B99", "PATTERNS")) for t in topics)

def test_census_api_label_mapping(tmp_path):
    (tmp_path / "2020_B01002E.json").write_text(json.dumps({"variables": {
        "B01002E_002E": {"label": "Estimate!!Median age --!!Male"}}}))
    assert load_census_api_labels(tmp_path, 2020) == {"B01002Ee2": "Median age --!!Male"}

def test_geography_counts():
    g = load_geography(Path("definitions/source/geography"), 2020)
    assert len(g) == 3234 and sum(c.block_groups == 0 for c in g) == 13
    assert sum(c.block_groups for c in g) == 242335
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement `sources.py`; run both scripts once (main checkout has Keychain access): `.venv/bin/python -m scripts.fetch_census_api` and `.venv/bin/python -m scripts.extract_geography`; commit the generated source files.
- [ ] **Step 4:** run → PASS; 2019 geography: `len == 3233`, block groups sum `220333`.
- [ ] **Step 5:** Commit `feat: builder source inputs`.

### Task 4: Member conversion (v1 → LLM-facing members)

**Files:**
- Create: `census_compass/semantic/members.py`
- Test: `tests/unit/test_members.py`

**Interfaces:**
- Consumes: v1 table dicts (as loaded by `census_compass.semantic.catalog.load_table_schema`), `load_census_api_labels`, `race_groups`.
- Produces: `classify_group(group_title: str) -> Literal["sum", "dimension"]`; `convert_group(v1_table: dict, group: str, year: int, api_labels: dict[str,str]) -> tuple[list[Dimension], list[Measure], list[MemberMetadata]]`; `describe(group_title, universe, path, race_code) -> str`.

Rules (spec §4.2): titles containing `Median`, `Quartile`, `Per Capita`, `Average`, `Gini`, `Ratio` → dimension (`type: number`); `Aggregate` or plain counts → measure `type: sum`; MOE never a member, only `meta.moe_sql`; published per capita income and average household size dimensions renamed `block_group_<name>`; race-version members get `meta.population_group`; E columns take descriptions from `api_labels`; description = group title + `Universe:` + breakdown path + race synonyms.

- [ ] **Step 1: Write failing tests**
```python
def test_median_is_dimension_and_count_is_sum(v1_b01_2020):
    dims, measures, _ = convert_group(v1_b01_2020, "B01002", 2020, {})
    assert all(d.type == "number" for d in dims if d.name != "census_block_group")
    _, m2, _ = convert_group(v1_b01_2020, "B01001", 2020, {})
    assert {m.type for m in m2} == {"sum"}
    assert m2[0].sql == '"2020_CBG_B01"."B01001e1"' and m2[0].meta["moe_sql"] == '"2020_CBG_B01"."B01001m1"'

def test_aggregate_income_is_sum():
    assert classify_group("Aggregate Household Income In The Past 12 Months") == "sum"
    assert classify_group("Per Capita Income In The Past 12 Months") == "dimension"

def test_published_per_capita_renamed(v1_b19_2020):
    dims, _, _ = convert_group(v1_b19_2020, "B19301", 2020, {})
    assert dims[0].name.startswith("block_group_")

def test_e_columns_use_api_labels_and_race_tag(v1_b25_2020):
    _, ms, meta = convert_group(v1_b25_2020, "B25003E", 2020,
                                {"B25003Ee2": "Total:!!Owner occupied"})
    assert "Owner occupied" in ms[1].description and "Pacific Islander" in ms[1].description
    assert ms[1].meta["population_group"] == "E" and meta[1].definition_source == "census_api"

def test_no_completeness_fields_in_llm_members(v1_b25_2020):
    _, ms, _ = convert_group(v1_b25_2020, "B25003", 2020, {})
    assert all(not ({"coverage_sql", "missing_sql"} & m.meta.keys()) for m in ms)
```
(fixtures load the real `data/tables/2020_CBG_B01.yaml` etc.)
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run → PASS.
- [ ] **Step 5:** Commit `feat: convert v1 groups to LLM-facing members`.

### Task 5: Hand-written measures (templates, validity predicate, coverage)

**Files:**
- Create: `definitions/source/measures.yaml`
- Create: `census_compass/semantic/measures.py`
- Test: `tests/unit/test_measures.py`, `tests/fixtures/measures/*.yaml`

**Template format** (`measures.yaml` entry):
```yaml
- name: unemployment_rate
  topic: labor_force_status
  title: Unemployment rate
  description: Unemployed people as a percentage of the civilian labor force.
  numerator: [B23025e5]
  denominator: [B23025e3]          # "-COL" subtracts
  scale: 100
  format: percent
  universe: Civilian labor force
  page: "68"
  for_each: null                    # or race_groups; "{race}" placeholder in column codes
  sparse: false                     # true → description adds "not published for every block group"
```
Columns are bare codes; the builder finds each column's physical table for the year (codes are unique per year) and generates a one-to-one join on `CENSUS_BLOCK_GROUP` for any second table.

**Measures to author (codes verified during design):**

| name | numerator | denominator | scale |
|---|---|---|---|
| sex_ratio | B01001e2 | B01001e26 | 100 |
| age_dependency_ratio | B01001e3–e6, e20–e25, e27–e30, e44–e49 | B01001e7–e19, e31–e43 | 100 |
| child_dependency_ratio | B01001e3–e6, e27–e30 | same 18–64 set | 100 |
| old_age_dependency_ratio | B01001e20–e25, e44–e49 | same 18–64 set | 100 |
| mean_travel_time_to_work | B08135e1 | B08303e1 | 1 |
| high_school_or_higher_pct | B15003e17–e25 | B15003e1 | 100 |
| bachelors_or_higher_pct | B15003e22–e25 | B15003e1 | 100 |
| poverty_rate | B17021e2 | B17021e1 | 100 |
| family_poverty_rate | B17010e2 | B17010e1 | 100 |
| mean_household_income | B19025e1 | B19001e1 | 1 |
| mean_household_income_{slug} (for_each, sparse) | B19025{race}e1 | B25003{race}e1 | 1 |
| mean_family_income | B19127e1 | B19101e1 | 1 |
| per_capita_income | B19313e1 | B01003e1 | 1 |
| mean_household_earnings | B19061e1 | B19051e2 | 1 |
| mean_earnings / _male / _female | B20003e1 / e2 / e5 | B20001e1 / e2 / e23 | 1 |
| unemployment_rate | B23025e5 | B23025e3 | 100 |
| labor_force_participation_rate | B23025e2 | B23025e1 | 100 |
| homeowner_vacancy_rate | B25004e4 | B25003e2, B25004e4, B25004e5 | 100 |
| rental_vacancy_rate | B25004e2 | B25003e3, B25004e2, B25004e3 | 100 |
| average_household_size / _owner / _renter | B25008e1 / e2 / e3 | B25003e1 / e2 / e3 | 1 |
| homeownership_rate | B25003e2 | B25003e1 | 100 |
| renters_paying_30_percent_or_more | B25070e7–e10 | B25070e1, -B25070e11 | 100 |
| crowded_homes_pct | B25014e5–e7, e11–e13 | B25014e1 | 100 |
| uninsured_pct | B27010e17, e33, e50, e66 | B27010e1 | 100 |
| limited_english_households_pct | C16002e4, e7, e10, e13 | C16002e1 | 100 |
| no_internet_access_pct | B28002e13 | B28002e1 | 100 |
| no_internet_subscription_pct | B28002e12, e13 | B28002e1 | 100 |

**Interfaces:**
- Produces: `load_templates(path) -> list[MeasureTemplate]`; `expand(templates, race_groups) -> list[MeasureTemplate]`; `render(template, year, column_tables: dict[str,str]) -> tuple[Measure, list[Join], MemberMetadata]` where `sql` = `scale * SUM(IFF(<any input NULL>, NULL, <num>)) / NULLIF(SUM(IFF(<any input NULL>, NULL, <den>)), 0)` and `MemberMetadata.coverage_sql` = `SUM(IFF(<any input NULL>, NULL, <den>)) / NULLIF(SUM(<den>), 0)`; `normalized_sql` = `sqlglot.transpile(sql, read="snowflake", pretty=False)[0]`.

- [ ] **Step 1: Write failing tests** — unit render tests plus SQL-on-fixture tests executed with DuckDB-free pure Python evaluation through `sqlite3` (SQLite supports `SUM`, `NULLIF`, `IIF`; test helper rewrites `IFF`→`IIF` and strips quotes' table qualifiers):
```python
def test_render_unemployment_sql_and_coverage():
    m, joins, meta = render(T["unemployment_rate"], 2020, {"B23025e5": "2020_CBG_B23", "B23025e3": "2020_CBG_B23"})
    assert joins == [] and m.type == "number" and m.format == "percent"
    assert meta.numerator_columns == ["2020_CBG_B23.B23025e5"] and meta.coverage_sql

def test_cross_table_join_generated():
    m, joins, _ = render(T["per_capita_income"], 2020, {"B19313e1": "2020_CBG_B19", "B01003e1": "2020_CBG_B01"})
    assert joins[0].relationship == "one_to_one"
    assert joins[0].sql == '"2020_CBG_B19"."CENSUS_BLOCK_GROUP" = "2020_CBG_B01"."CENSUS_BLOCK_GROUP"'

@pytest.mark.parametrize("rows,expected_value,expected_coverage", [
    ([(10, 100), (20, 100)], 15.0, 1.0),          # complete
    ([(None, 100), (20, 100)], 20.0, 0.5),        # missing numerator
    ([(10, None), (20, 100)], 20.0, 1.0),         # missing denominator (unknown denominator)
    ([(0, 0)], None, None),                        # zero denominator → not available
    ([(None, None)], None, None),                  # no valid rows
])
def test_validity_predicate_on_fixture(rows, expected_value, expected_coverage): ...

def test_race_template_expands_to_nine_tagged_measures():
    names = [t.name for t in expand([T["mean_household_income_{slug}"]], RACE_GROUPS)]
    assert len(names) == 9 and "mean_household_income_asian_alone" in names

def test_block_group_without_published_income_is_not_zero():
    # 238-row case: aggregate income NULL, population > 0 → per_capita_income NULL
    assert evaluate(T["per_capita_income"], [(None, 25)]) is None
```
- [ ] **Step 2:** run → FAIL. **Step 3:** implement `measures.py` and author `measures.yaml` from the table. **Step 4:** run → PASS.
- [ ] **Step 5:** Commit `feat: hand-written measure templates with validity predicate and coverage`.

### Task 6: Topic file generation and build checks

**Files:**
- Create: `census_compass/semantic/build.py`
- Test: `tests/unit/test_build.py`

**Interfaces:**
- Consumes: Tasks 3–5.
- Produces: `generate(settings, year) -> tuple[list[TopicFile], BuilderMetadata]`; `run_checks(files, metadata, topics, v1_tables, settings) -> list[str]` (empty = pass); `size_warnings(files, settings) -> list[str]` (topics over `topic_token_limit`, never an error); `write_outputs(files, metadata, allowed, out_dir)` writing `definitions/generated/{year}/{topic}_{year}.yaml` (topic names already ending in `_2020` are not suffixed again), `definitions/generated/{year}/builder_metadata.json`, `definitions/generated/allowed_tables.json`. Internal topics (`geography`, `land_area_and_location`) are generated with `internal: true` written to metadata only (not embedded).

Checks (spec §5): every Census group of included tables in exactly one topic, both years; one physical table per topic; every measure column in its file or a declared joined table; every referenced column exists in `table_columns`; every hand-written ratio has `coverage_sql` and input columns; no LLM file contains `coverage_sql`/`missing_sql`/`definition_source`; every file validates against `definitions/schema/topic_file.schema.json`; all 148 E columns per year have descriptions; excluded and geometry tables absent from `allowed_tables.json`.

- [ ] **Step 1: Write failing tests**
```python
def test_real_build_passes_all_checks(settings):
    for year in (2019, 2020):
        files, meta = generate(settings, year)
        assert run_checks(files, meta, load_topics(...), ..., settings) == []

def test_oversize_topic_warns_but_does_not_fail(settings, monkeypatch):
    monkeypatch.setattr(settings, "topic_token_limit", 100)
    files, meta = generate(settings, 2020)
    assert run_checks(files, meta, ...) == []                     # not an error
    warnings = size_warnings(files, settings)
    assert any("rent_and_rent_burden_2020" in w and "tokens" in w for w in warnings)

def test_unassigned_group_detected(settings):
    errors = run_checks(..., topics=topics_without("B25004"))
    assert any("B25004" in e and "not assigned" in e for e in errors)

def test_allowed_tables_exclude_blocked():
    allowed = json.loads(Path("definitions/generated/allowed_tables.json").read_text())
    assert "2020_CBG_B99" not in allowed["2020"] and "2019_CBG_PATTERNS" not in allowed["2019"]
```
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. Oversized topics only produce warnings (user decision: no splitting for the MVP). **Step 4:** run → PASS.
- [ ] **Step 5:** Commit `feat: topic file generation and build checks` (generated files committed in Task 18, not here).

### Stream B — Resolvers (Tasks 7–9)

### Task 7: resolve_year

**Files:** Create `census_compass/tools/year.py`; Test `tests/unit/test_year.py`

**Interfaces:** Produces `resolve_year(text: str | None) -> dict` (a `YearResolution.model_dump()`); pure function, no I/O.

- [ ] **Step 1: Write failing tests** — one parametrized test per spec §7 row:
```python
@pytest.mark.parametrize("text,status,years,comparison", [
    (None, "ok", [2020], False), ("latest", "ok", [2020], False), ("current", "ok", [2020], False),
    ("2019", "ok", [2019], False), ("2020", "ok", [2020], False),
    ("2016", "ambiguous", [], False), ("2017", "ambiguous", [], False), ("2018", "ambiguous", [], False),
    ("2015", "ok", [2019], False), ("2010", "unavailable", [], False), ("2023", "unavailable", [], False),
    ("from 2019 to 2020", "ok", [2019, 2020], True),
])
def test_year_rules(text, status, years, comparison): ...
def test_2020_has_both_sources():
    assert resolve_year("2020")["periods"][0]["sources"] == ["acs", "census_count"]
def test_labels():
    assert [p["label"] for p in resolve_year("from 2019 to 2020")["periods"]] == ["2015–2019", "2016–2020"]
```
- [ ] **Steps 2–4:** fail → implement (docstring: purpose and rules, it is what the LLM reads) → pass.
- [ ] **Step 5:** Commit `feat: resolve_year`.

### Task 8: resolve_population_group

**Files:** Create `census_compass/tools/population_group.py`; Test `tests/unit/test_population_group.py`

**Interfaces:** Consumes `RACE_GROUPS`. Produces `resolve_population_group(text: str) -> dict` (`PopulationGroupResolution`); case-insensitive match on label, slug, synonyms; "White" / "white people" → `ambiguous` with candidates A and H labels; "Asian alone or in combination" → `not_found` with message that only "alone" groups have income measures.

- [ ] **Step 1: tests** — every label and synonym resolves to its code (parametrized over `RACE_GROUPS`); `"White"` ambiguous with 2 candidates; `"Martian"` → `not_found`.
- [ ] **Steps 2–5:** fail → implement → pass → commit `feat: resolve_population_group`.

### Task 9: resolve_geography

**Files:** Create `census_compass/tools/geography.py`, `census_compass/geography_names.py` (static 52 state names ↔ abbreviations ↔ FIPS); Test `tests/unit/test_geography.py`

**Interfaces:** Consumes `contracts.load_geography(source_dir, year)` (Task 2) — tests use a fixture file in that format so this stream does not wait for Task 3. Produces `resolve_geography(place: str, year: int) -> dict` (`GeographyResolution`); `sql_filter` uses the literal placeholder `{table}`.

- [ ] **Step 1: Write failing tests** (fixture with Cook IL, Orange in CA/FL/…, Doña Ana NM, St. Louis city MO and St. Louis County MO, one county with `block_groups: 0`):
```python
def test_county(): r = resolve_geography("Cook County, Illinois", 2020)
    assert r["sql_filter"] == "LEFT({table}.\"CENSUS_BLOCK_GROUP\", 5) = '17031'" and r["level"] == "county"
def test_state(): assert resolve_geography("Texas", 2020)["sql_filter"] == "LEFT({table}.\"CENSUS_BLOCK_GROUP\", 2) = '48'"
def test_nation(): r = resolve_geography("United States", 2020)
    assert r["level"] == "nation" and r["sql_filter"] is None and "Puerto Rico" in r["name"]
def test_ambiguous(): r = resolve_geography("Orange County", 2020)
    assert r["status"] == "ambiguous" and len(r["candidates"]) >= 2
def test_case_and_punctuation(): assert resolve_geography("cook county il", 2020)["status"] == "ok"
    assert resolve_geography("Dona Ana County, NM", 2020)["status"] == "ok"
def test_city_vs_county(): assert resolve_geography("St. Louis city, MO", 2020)["sql_filter"].endswith("'29510'")
def test_block_group_id_text(): assert resolve_geography("010010201001", 2020)["level"] == "block_group"
def test_unknown_id(): assert resolve_geography("999999999999", 2020)["status"] == "not_found"
def test_no_data(): assert resolve_geography("<fixture county with 0>", 2020)["status"] == "no_data"
def test_unsupported(): assert resolve_geography("Austin", 2020)["status"] == "unsupported"
    assert resolve_geography("ZIP 10001", 2020)["status"] == "unsupported"
```
Unknown/renamed county names → `not_found` with up to 3 closest names (`difflib.get_close_matches`). Real-data tests (skipped if `definitions/source/geography` absent): every county in each year's file resolves to its own 5-digit filter; every county name shared by several states (bare name, no state) returns `ambiguous` with **all** of them as candidates; every county with `block_groups == 0` returns `no_data` (criterion 7).
- [ ] **Steps 2–5:** fail → implement (docstring documents the 12-digit ID structure and the county-name join, spec §7/§8) → pass → commit `feat: resolve_geography`.

### Stream C — Query safety and execution (Tasks 10–13)

### Task 10: Snowflake connection and request budget

**Files:** Modify `census_compass/database/connection.py`; Create `census_compass/budget.py`; Test `tests/unit/test_budget.py`, `tests/unit/test_connection.py`

**Interfaces:**
- Produces: `RequestBudget(total_seconds: float, clock=time.monotonic)` with `remaining() -> float`, `expired() -> bool`, `statement_timeout(minimum: int) -> int | None` (None when remaining < minimum); `open_connection(settings) -> SnowflakeConnection` (Keychain password, role check, `USE SECONDARY ROLES NONE`, raises if role differs); `run_statement(conn, sql, timeout_s, max_rows) -> tuple[list[str], list[tuple], bool, str]` (columns, rows, truncated, query_id; sets `STATEMENT_TIMEOUT_IN_SECONDS` per statement); `cancel(conn, query_id) -> None` (`SELECT SYSTEM$CANCEL_QUERY(...)`).
- [ ] **Step 1: tests** — budget with fake clock: after 56 s, `statement_timeout(5)` is None; after 10 s → 50. Connection tests with a fake connector object: secondary roles statement executed; wrong role raises `RuntimeError`; truncated true when rows > max_rows.
- [ ] **Steps 2–5:** fail → implement → pass → commit `feat: snowflake connection invariants and request budget`.

### Task 11: SQL validation (sqlglot, fail-closed)

**Files:** Modify `census_compass/database/validation.py`; Test `tests/unit/test_validation.py`

**Interfaces:**
- Consumes: `BuilderMetadata`, `AllowedTables`, `QueryRejected` (tests build small fake metadata).
- Produces: `validate_and_fix(sql: str, metadata: BuilderMetadata, allowed: list[str], max_rows: int, database: str, schema: str) -> str` (fixed SQL) or raises `QueryRejected`. Applies spec §9.3 to every scope via `sqlglot.optimizer.scope.traverse_scope`; aliases rewritten to real names, repeated table → reject; re-validates after fixing; dimension set = members with `kind == "dimension"` mapped to `TABLE.COLUMN`.
- [ ] **Step 1: Write failing tests** (one per row of §9.3 plus bypass attempts):
```python
REJECT = {
  "multi": 'SELECT 1; SELECT 2',
  "dml": 'DELETE FROM "2020_CBG_B01"',
  "excluded": 'SELECT * FROM "2020_CBG_B99"',
  "unknown_col": 'SELECT "2020_CBG_B01"."B01001e999" FROM "2020_CBG_B01"',
  "mix_years": 'SELECT 1 FROM "2019_CBG_B01" JOIN "2020_CBG_B01" ON "2019_CBG_B01"."CENSUS_BLOCK_GROUP" = "2020_CBG_B01"."CENSUS_BLOCK_GROUP"',
  "mix_sources": 'SELECT 1 FROM "2020_REDISTRICTING_CBG_DATA" JOIN "2020_CBG_B01" ON ...',
  "bad_join": '... JOIN "2020_CBG_B19" ON "2020_CBG_B01"."B01001e1" = "2020_CBG_B19"."B19001e1"',
  "avg_median": 'SELECT AVG("2020_CBG_B19"."B19013e1") FROM "2020_CBG_B19"',
  "cte_median": 'WITH x AS (SELECT "2020_CBG_B19"."B19013e1" AS v FROM "2020_CBG_B19") SELECT AVG(v) FROM x',
  "window_median": 'SELECT SUM("2020_CBG_B19"."B19013e1") OVER () FROM "2020_CBG_B19"',
  "self_join": 'SELECT 1 FROM "2020_CBG_B01" a JOIN "2020_CBG_B01" b ON a."CENSUS_BLOCK_GROUP" = b."CENSUS_BLOCK_GROUP"',
  "untraceable": 'SELECT SUM(v) FROM (SELECT * FROM "2020_CBG_B19") t',
}
@pytest.mark.parametrize("name", REJECT)
def test_rejected(name): with pytest.raises(QueryRejected): validate_and_fix(REJECT[name], META, ALLOWED, 1000, DB, SCHEMA)

def test_fixes_quoting_prefix_alias_and_limit():
    out = validate_and_fix('select sum(t."B01001e1") from "2020_CBG_B01" t', META, ALLOWED, 1000, DB, SCHEMA)
    assert f'"{DB}"."{SCHEMA}"."2020_CBG_B01"' in out and " t" not in out and "LIMIT 1000" in out

def test_fips_join_allowed():
    validate_and_fix('SELECT "2020_METADATA_CBG_FIPS_CODES"."COUNTY", SUM("2020_CBG_B01"."B01003e1") FROM "2020_CBG_B01" '
        'JOIN "2020_METADATA_CBG_FIPS_CODES" ON LEFT("2020_CBG_B01"."CENSUS_BLOCK_GROUP", 5) = '
        '"2020_METADATA_CBG_FIPS_CODES"."STATE_FIPS" || "2020_METADATA_CBG_FIPS_CODES"."COUNTY_FIPS" GROUP BY 1', META, ALLOWED, 1000, DB, SCHEMA)

def test_ranking_cte_allowed():  # Review Focus 3
    validate_and_fix(RANK_TOP10_UNEMPLOYMENT_SQL, META, ALLOWED, 1000, DB, SCHEMA)

def test_unknown_column_suggests_close_match():
    with pytest.raises(QueryRejected) as e: validate_and_fix(UNKNOWN_COL_TYPO_SQL, ...)
    assert "B01001e1" in e.value.suggestions
```
- [ ] **Steps 2–5:** fail → implement → pass → commit `feat: fail-closed SQL validation`.

### Task 12: Completeness augmentation

**Files:** Create `census_compass/database/completeness.py`; Test `tests/unit/test_completeness.py`

**Interfaces:**
- Consumes: validated SQL from Task 11, `BuilderMetadata`.
- Produces: `augment(sql: str, metadata: BuilderMetadata) -> AugmentedQuery` (`sql: str`, `companions: list[Companion]`; `Companion(alias, output_column, kind: Literal["block_groups","missing_input","coverage","unknown_denominator","missing_moe"], input_column: str | None)`), raises `QueryRejected` when companions cannot be carried (spec §9.5 step 3); `split_result(columns, rows, companions) -> tuple[list[dict], list[Completeness]]`. Hand-written ratios are recognized by comparing the normalized aggregate expression with `MemberMetadata.normalized_sql`.
- [ ] **Step 1: Write failing tests**
```python
def test_total_gets_block_groups_and_missing(): q = augment(TOTAL_POP_SQL, META)
    assert {c.kind for c in q.companions} == {"block_groups", "missing_input"}

def test_grouped_query_companions_share_group_by():
    q = augment(BY_COUNTY_SQL, META); tree = sqlglot.parse_one(q.sql, read="snowflake")
    assert tree.args["group"] is not None and all(c.alias in q.sql for c in q.companions)

def test_hand_written_ratio_gets_coverage(): q = augment(UNEMPLOYMENT_SQL, META)
    assert {"coverage", "unknown_denominator"} <= {c.kind for c in q.companions}

def test_moe_sum_gets_missing_moe(): assert "missing_moe" in {c.kind for c in augment(MOE_SQL, META).companions}

def test_rank_top10_carries_companions(): augment(RANK_TOP10_UNEMPLOYMENT_SQL, META)  # passes through

def test_reaggregation_rejected():
    with pytest.raises(QueryRejected): augment('SELECT AVG(r) FROM (' + BY_COUNTY_RATE_SQL + ') s', META)

def test_split_removes_companions():
    rows, comp = split_result(["county","rate","__bg_rate","__miss_rate_B23025e5"], [("Cook", 7.1, 4002, 0)], COMPANIONS)
    assert rows == [{"county": "Cook", "rate": 7.1}] and comp[0].block_groups == 4002
```
- [ ] **Steps 2–5:** fail → implement → pass → commit `feat: completeness augmentation`.

### Task 13: execute_query tool

**Files:** Modify `census_compass/tools/query.py`; Test `tests/unit/test_query_tool.py`

**Interfaces:**
- Consumes: Tasks 10–12; `contracts.load_builder_metadata` / `load_allowed_tables` from `settings.semantic_dir` (tests use fixture files).
- Produces: `execute_query(sql: str, tool_context: ToolContext) -> dict` (`QueryResult.model_dump()`); budget read from `tool_context.state["temp:budget_deadline"]` (monotonic deadline set by the agent, Task 20); retries counted in `tool_context.state["temp:sql_attempts"]`.
- Behavior: validation/augmentation failure → `QUERY_REJECTED` with reason and suggestions (no database call); budget below minimum → `TIMED_OUT` without execution; timeout → cancel by query ID, `TIMED_OUT`, never resubmitted; connection/login error → `DATABASE_FAILURE`; rows > max → `truncated: true` and message; attempts > `max_sql_retries + 1` → `QUERY_REJECTED` "retry limit".
- [ ] **Step 1: tests** with fake connection and fake clock: each outcome above; `completeness` present for every aggregate output; no `run_statement` call for rejected SQL.
- [ ] **Steps 2–5:** fail → implement → pass → commit `feat: execute_query tool`.

### Stream D — Search (Tasks 14–15)

### Task 14: Index build, search_schema and get_schema

**Files:** Modify `census_compass/semantic/embeddings.py`, `census_compass/semantic/retrieval.py`, `census_compass/tools/schema.py`; Create `census_compass/semantic/index.py`; Test `tests/unit/test_search.py`

**Interfaces:**
- Consumes: `contracts.load_topic_files` / `load_builder_metadata` over `{dir}/{year}/*.yaml` (tests use 4 small fixture topic files).
- Produces: `build_index(semantic_dir: Path, index_dir: Path, model_name: str) -> None` (two LanceDB tables `files` and `members` with columns `year`, `file`, `member`, `title`, `text`, `population_group`, `vector`; native FTS index on `text`; written to `index_dir.with_suffix(".tmp")` then renamed over `index_dir`); `search(query: str, year: int, population_group: str | None, k: int = 5) -> list[SearchHit]` (hybrid search with `RRFReranker`, members filtered `population_group IS NULL OR population_group = ?`, member hits aggregated to files, top 5 files, ≤5 members each); tools `search_schema(query: str, year: int, population_group: str | None = None) -> dict` and `get_schema(names: list[str]) -> dict` (`{"files": [...], "errors": [{"name": ..., "valid_names": [...]}]}`).
- [ ] **Step 1: Write failing tests** (fixture index built with the smallest candidate model in a temp dir):
```python
def test_year_filter(): assert all(h.name.endswith("_2020") for h in search("unemployment", 2020, None))
def test_race_filter_excludes_other_races():
    hits = search("household income", 2020, "D")
    assert all(m.name.endswith(("asian_alone", "income")) for h in hits for m in h.matched_members)
def test_race_query_without_race_measure_returns_general_topic():  # Review Focus 2
    assert any(h.name == "poverty_2020" for h in search("poverty rate", 2020, "D"))
def test_internal_files_not_indexed(): assert not any("geography" in h.name for h in search("county", 2020, None))
def test_get_schema_unknown_name(): out = get_schema(["nope_2020"])
    assert out["errors"][0]["name"] == "nope_2020" and out["errors"][0]["valid_names"]
def test_atomic_swap_keeps_old_index_on_failure(tmp_path, monkeypatch): ...
def test_empty_result(): assert search("zzzz qqqq", 2020, None) == [] or all(...)
```
- [ ] **Steps 2–5:** fail → implement → pass → commit `feat: hybrid search index and schema tools`.

### Task 15: Embedding model selection

**Files:** Create `evaluations/search_dev.yaml` (≥ 40 questions), `evaluations/search_heldout.yaml` (≥ 60 questions, never used for selection), `scripts/select_embedding_model.py`; Test `tests/unit/test_search_sets.py`

**Interfaces:** question entry `{id, question, year, population_group, required_files: [..]}` (plain English, paraphrased — no Census codes); `scripts/select_embedding_model.py --candidates BAAI/bge-small-en-v1.5 thenlper/gte-small sentence-transformers/all-MiniLM-L6-v2` prints recall@5 (all required files in top 5) per model on the **dev** set only.
- [ ] **Step 1: test** — the two sets share no question ids or texts; every `required_files` name exists among topic names; held-out ≥ 60, dev ≥ 40; both years represented.
- [ ] **Step 2:** fail → write sets → pass.
- [ ] **Step 3:** after Task 6 output exists: run the script, record results in the plan ledger, set `EMBEDDING_MODEL` in `.env.example` to the best model.
- [ ] **Step 4:** Commit `feat: search question sets and embedding model choice`.

### Stream E — Rules and evaluations (Tasks 16–17)

### Task 16: Domain rules and agent instructions

**Files:** Create `census_compass/prompts/domain_rules.md`; Modify `census_compass/prompts/agent_instructions.md`; Test `tests/unit/test_prompts.py`

**Content:** `domain_rules.md` = spec §10 rules A–E, plain sentences, ≤ 1,500 tokens (characters ÷ 4), no Census codes except `CENSUS_BLOCK_GROUP` and the two join forms. `agent_instructions.md`: tool order (year and population group first, then geography per period, search, get_schema, SQL, execute_query), clarification-before-query, conversation-state rule (latest explicit statement wins), answer format (source, period, completeness, ±), terminal outcome messages from spec §9.4.
- [ ] **Step 1: tests** — rules file ≤ 1,500 tokens; contains each required phrase: "US including Puerto Rico", "not adjusted for inflation", "90%", "CENSUS_BLOCK_GROUP", "never aggregated"; instructions mention all six tool names.
- [ ] **Steps 2–5:** fail → write → pass → commit `feat: domain rules and agent instructions`.

### Task 17: End-to-end evaluation set and published-value fixtures

**Files:** Modify `evaluations/questions.yaml` (≥ 40 cases); Create `scripts/fetch_published_values.py`, `tests/fixtures/published/{year}.json`; Test `tests/unit/test_eval_set.py`

**Interfaces:**
- Evaluation case: `{id, turns: [..], expected: {kind: number|refusal|clarification, value?, tolerance?, must_mention: [..], must_not: [..]}, traps: [..]}`. Must cover every trap in spec criterion 12 and multi-turn cases (change place, year, race; answer a clarification).
- Published fixtures: for each full-coverage measure in Task 5, United States and California (`state:06`), 2019 and 2020, value from the Census API ACS 5-year profile/subject tables. The script finds variables **by label** in `variables.json` (variable numbering differs between years) and saves `{measure, geography, year, variable, label, value}`; the human reviews labels before commit. Note: published "United States" excludes Puerto Rico — the acceptance test (Task 19) filters `LEFT(id, 2) <> '72'` for the US comparison only.
- [ ] **Step 1: tests** — every trap id present; ≥ 40 cases; every fixture entry has a non-empty label and numeric value; both years and both geographies for every full-coverage measure.
- [ ] **Steps 2–5:** fail → author set, run fetch script, review labels → pass → commit `feat: evaluation set and published-value fixtures`.

---

## Phase 2 — Integration (sequential, after merging all streams)

### Task 18: Single build command and generated definitions

**Files:** Create `scripts/build_semantic.py` (replaces the placeholder `scripts/build_index.py`, which is deleted); Modify `README.md` (commands section); Test `tests/integration/test_build_command.py`

**Interfaces:** `python -m scripts.build_semantic` → `generate` + `run_checks` for both years (non-zero exit with all errors on failure, nothing written) → `write_outputs` → `build_index` (atomic) → prints counts.
- [ ] **Step 1: test** — running the command in a temp output dir produces both years' files, metadata, `allowed_tables.json` and an index; a forced check failure leaves the previous outputs untouched.
- [ ] **Steps 2–4:** fail → implement → pass; run for real; `git add definitions/generated` (index stays in ignored `data/index`).
- [ ] **Step 5:** Commit `feat: single build command and generated definitions`.

### Task 19: Measure acceptance against Snowflake (criterion 5)

**Files:** Create `tests/integration/test_published_values.py` (marker `snowflake`, skipped without Keychain access)
- [ ] **Step 1:** for each full-coverage measure × {US excl. PR, California} × {2019, 2020}: build the SQL from the generated measure, run through `execute_query`'s validation + connection, compare to fixture within 0.2 pp (rates), 0.5% (dollar/minute means), 0.01 (ratios, household size). No exceptions.
- [ ] **Step 2:** run `.venv/bin/python -m pytest -m snowflake tests/integration/test_published_values.py -v` → Expected: all PASS. A failure is fixed in the measure template (Task 5 files), never by widening tolerance.
- [ ] **Step 3:** Commit `test: published-value acceptance for hand-written measures`.

### Task 20: ADK agent wiring

**Files:** Modify `census_compass/agent.py`; Test `tests/unit/test_agent.py`

**Interfaces:** `root_agent: LlmAgent` with `model=LiteLlm(model=settings.agent_model)`, instruction = `agent_instructions.md` + `domain_rules.md`, tools = the six functions; `before_agent_callback` sets `temp:budget_deadline = time.monotonic() + request_budget_seconds` and `temp:sql_attempts = 0`; `after_model_callback`/error handling maps provider errors to the `MODEL_FAILURE` message. `adk web` discovers `root_agent`.
- [ ] **Step 1: tests** — agent has exactly the six tools by name; instruction contains the rules text; callback sets both state keys; with a fake model emitting a tool call sequence, `resolve_geography` is never called before `resolve_year` returns a final period (criterion 8), and no `execute_query` call occurs while a clarification is pending (criterion 12).
- [ ] **Step 2–4:** fail → implement → pass; manual smoke: `adk web` → ask "What is the unemployment rate in Cook County, Illinois?" → answer names ACS 2016–2020 and includes the rate. Requires `ANTHROPIC_API_KEY` in `.env` (user supplies).
- [ ] **Step 5:** Commit `feat: wire ADK agent`.

### Task 21: End-to-end acceptance run

**Files:** Create `scripts/run_evaluation.py`, `evaluations/reports/` (gitignored outputs)
- [ ] **Step 1:** runner executes every case through `InMemoryRunner`, records answer, tool calls, outcome and elapsed time; grades numbers (tolerance), required mentions, forbidden content, trap avoidance, clarification-before-query; computes criteria 11–13 (search recall on the held-out set; ≥ 90% correct; 100% traps; 100% source/period/dollar-year; 100% completeness reporting; 0 queries during pending clarification; ≥ 95% within 60 s, all end with a terminal outcome).
- [ ] **Step 2:** run → Expected: report shows every criterion met. Any miss: record, fix in the owning task's files, re-run. Thresholds are not changed (frozen).
- [ ] **Step 3:** Commit `test: end-to-end acceptance runner and report`; then use superpowers:finishing-a-development-branch.

---

## Parallel execution map

| Phase | Work | Worktree / branch | Depends on |
|---|---|---|---|
| 0 | Tasks 0–2 | main checkout, `feature/census-agent` | user approval of commit |
| 1 | Stream A: Tasks 3–6 | `stream/builder` | Phase 0 |
| 1 | Stream B: Tasks 7–9 | `stream/resolvers` | Phase 0 (Task 9 uses its own fixture) |
| 1 | Stream C: Tasks 10–13 | `stream/query-safety` | Phase 0 |
| 1 | Stream D: Task 14 | `stream/search` | Phase 0 (fixture topic files) |
| 1 | Stream E: Tasks 16–17 | `stream/rules-evals` | Phase 0 |
| 1b | Task 15 step 3 (model choice) | `stream/search` | Stream A merged |
| 2 | Tasks 18–21 | `feature/census-agent` | all streams merged |

Streams own disjoint files; merges into `feature/census-agent` in order A, B, C, D, E; full `pytest` and `ruff check .` after each merge.
