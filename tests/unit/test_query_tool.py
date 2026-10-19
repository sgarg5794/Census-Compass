import inspect
import json
import warnings
from decimal import Decimal

import pytest
import sqlglot
from snowflake.connector.errors import OperationalError, ProgrammingError

from census_compass.config import Settings
from census_compass.contracts import BuilderMetadata, MemberMetadata, QueryResult
from census_compass.tools import query
from census_compass.tools.query import execute_query, run_query

PASSWORD = "s3cret-Pa55word"
B23 = "2020_CBG_B23"
FIPS20 = "2020_METADATA_CBG_FIPS_CODES"
B01_19 = "2019_CBG_B01"
FIPS19 = "2019_METADATA_CBG_FIPS_CODES"
FIPS_JOIN = (
    f'JOIN "{FIPS20}" f ON LEFT(e."CENSUS_BLOCK_GROUP", 5) = f."STATE_FIPS" || f."COUNTY_FIPS"'
)
BY_COUNTY = (
    f'SELECT f."COUNTY", SUM(e."B23025e5") AS unemployed, '
    f'100 * SUM(e."B23025e5") / NULLIF(SUM(e."B23025e3"), 0) AS rate '
    f'FROM "{B23}" e {FIPS_JOIN} GROUP BY f."COUNTY"'
)
PLAIN = f'SELECT e."B23025e5" FROM "{B23}" e'
POP_2019 = f'SELECT SUM(p."B01001e1") AS pop FROM "{B01_19}" p'


def _member(table, column, kind):
    return MemberMetadata(
        file="f",
        member=f"{table}_{column}".lower(),
        kind=kind,
        table=table,
        source_column=column,
        aggregation="sum" if kind == "measure" else "none",
        definition_source="dictionary",
    )


def _write_semantic(root):
    meta = {
        2019: BuilderMetadata(
            year=2019,
            members=[
                _member(B01_19, "CENSUS_BLOCK_GROUP", "dimension"),
                _member(B01_19, "B01001e1", "measure"),
                _member(FIPS19, "COUNTY", "dimension"),
            ],
            table_columns={
                B01_19: ["CENSUS_BLOCK_GROUP", "B01001e1"],
                FIPS19: ["STATE", "STATE_FIPS", "COUNTY_FIPS", "COUNTY"],
            },
            fips_table=FIPS19,
        ),
        2020: BuilderMetadata(
            year=2020,
            members=[
                _member(B23, "CENSUS_BLOCK_GROUP", "dimension"),
                _member(B23, "B23025e3", "measure"),
                _member(B23, "B23025e5", "measure"),
                _member(FIPS20, "STATE", "dimension"),
                _member(FIPS20, "COUNTY", "dimension"),
            ],
            table_columns={
                B23: ["CENSUS_BLOCK_GROUP", "B23025e3", "B23025e5"],
                FIPS20: ["STATE", "STATE_FIPS", "COUNTY_FIPS", "COUNTY"],
            },
            fips_table=FIPS20,
        ),
    }
    for year, m in meta.items():
        (root / str(year)).mkdir(parents=True)
        (root / str(year) / "builder_metadata.json").write_text(m.model_dump_json())
    (root / "allowed_tables.json").write_text(
        json.dumps({"2019": [B01_19, FIPS19], "2020": [B23, FIPS20]})
    )
    return root


# --------------------------------------------------------------------------- fakes


def _default_rows(columns):
    row = []
    for c in columns:
        if c.startswith("__CC_") or c.lower().startswith("__cc_"):
            row.append(Decimal("10") if "block_groups" in c.lower() else 0)
        elif c.upper() == "COUNTY":
            row.append("Cook County")
        else:
            row.append(Decimal("7.5"))
    return [tuple(row), tuple(row)]


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.sfqid = None
        self.description = None
        self._rows = []

    def execute(self, sql):
        self.conn.executed.append(sql)
        if sql.startswith(("ALTER SESSION", "USE SECONDARY")):
            return self
        if sql == "SELECT CURRENT_ROLE()":
            self._rows = [("CENSUS_READER",)]
            return self
        if "SYSTEM$CANCEL_QUERY" in sql:
            self.conn.cancelled.append(sql)
            return self
        self.conn.statements.append(sql)
        if self.conn.errors:
            err = self.conn.errors.pop(0)
            if err is not None:
                raise err
        self.sfqid = "01b2-0000-abcd"
        columns = [e.alias_or_name for e in sqlglot.parse_one(sql, read="snowflake").expressions]
        self.description = [(c,) for c in columns]
        self._rows = self.conn.responder(columns)
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchmany(self, n):
        return self._rows[:n]

    def close(self):
        pass


class FakeConn:
    def __init__(self, errors=None, responder=_default_rows):
        self.errors = list(errors or [])
        self.responder = responder
        self.executed, self.statements, self.cancelled = [], [], []
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


class Connector:
    """Hands out the given connections in order; records every login."""

    def __init__(self, *conns):
        self.conns = list(conns)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        item = self.conns.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture(autouse=True)
def _reset():
    query.reset()
    yield
    query.reset()


@pytest.fixture
def semantic(tmp_path):
    return _write_semantic(tmp_path / "semantic")


def _run(sql, semantic, connector, state=None, clock=None, settings=None):
    clock = clock or FakeClock()
    state = {"temp:budget_deadline": clock() + 60} if state is None else state
    out = run_query(
        sql,
        state,
        settings=settings or Settings(),
        semantic_dir=semantic,
        connect=connector,
        get_password=lambda account, user: PASSWORD,
        clock=clock,
    )
    QueryResult.model_validate(out)  # always a valid contract
    json.dumps(out)  # always JSON-serializable
    return out


# --------------------------------------------------------------------------- tool surface


def test_adk_signature_is_exactly_sql_and_tool_context():
    params = list(inspect.signature(execute_query).parameters)
    assert params == ["sql", "tool_context"]
    from google.adk.tools import FunctionTool

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        decl = FunctionTool(execute_query)._get_declaration()
    schema = decl.parameters_json_schema or decl.parameters.model_dump()
    assert list(schema["properties"]) == ["sql"]
    doc = execute_query.__doc__.lower()
    for word in ("completeness", "sql_filter", "retr", "truncated", "timed_out", "query_rejected"):
        assert word in doc


def test_execute_query_reads_state_from_tool_context(monkeypatch):
    seen = {}

    def fake(sql, state, **kwargs):
        seen.update(sql=sql, state=state)
        return {"outcome": "answered"}

    monkeypatch.setattr(query, "run_query", fake)

    class Ctx:
        state = {"temp:sql_attempts": 0}

    assert execute_query("SELECT 1", Ctx()) == {"outcome": "answered"}
    assert seen == {"sql": "SELECT 1", "state": Ctx.state}


# --------------------------------------------------------------------------- outcomes


def test_answered_with_completeness_for_every_aggregate(semantic):
    conn = FakeConn()
    out = _run(BY_COUNTY, semantic, Connector(conn))
    assert out["outcome"] == "answered" and out["truncated"] is False
    assert out["query_id"] == "01b2-0000-abcd"
    assert len(out["rows"]) == 2
    for row in out["rows"]:
        assert not any(k.lower().startswith("__cc_") for k in row)
        assert row["COUNTY"] == "Cook County"
    pairs = {(c["row"], c["column"].lower()) for c in out["completeness"]}
    assert pairs == {(r, col) for r in (0, 1) for col in ("unemployed", "rate")}
    assert all(c["block_groups"] == 10 for c in out["completeness"])
    # validated + augmented SQL ran with the session timeout set first
    assert len(conn.statements) == 1 and "COUNT(*)" in conn.statements[0].upper()
    assert any("STATEMENT_TIMEOUT_IN_SECONDS = 60" in s for s in conn.executed)


def test_aggregate_values_are_json_numbers(semantic):
    out = _run(BY_COUNTY, semantic, Connector(FakeConn()))
    assert out["rows"][0]["RATE"] == 7.5 and isinstance(out["rows"][0]["RATE"], float)


def test_merged_metadata_allows_2019_tables(semantic):
    out = _run(POP_2019, semantic, Connector(FakeConn()))
    assert out["outcome"] == "answered", out["message"]
    assert [c["column"].lower() for c in out["completeness"]] == ["pop", "pop"]


def test_empty_result_is_answered_no_rows(semantic):
    out = _run(BY_COUNTY, semantic, Connector(FakeConn(responder=lambda cols: [])))
    assert out["outcome"] == "answered"
    assert out["rows"] == [] and out["completeness"] == []
    assert out["message"] == "no rows"


def test_rejected_sql_never_reaches_database(semantic):
    connector = Connector(FakeConn())
    out = _run('SELECT e."B23025e9" FROM "2020_CBG_B23" e', semantic, connector)
    assert out["outcome"] == "query_rejected"
    assert "B23025e9" in out["message"]
    assert "B23025e5" in out["message"] or "B23025e3" in out["message"]  # suggestions
    assert connector.calls == []  # no login, no statement


def test_augmentation_rejection_never_reaches_database(semantic):
    nested = (
        f'SELECT SUM(t.u) AS total FROM (SELECT f."COUNTY", SUM(e."B23025e5") AS u '
        f'FROM "{B23}" e {FIPS_JOIN} GROUP BY f."COUNTY") t'
    )
    connector = Connector(FakeConn())
    out = _run(nested, semantic, connector)
    assert out["outcome"] == "query_rejected"
    assert connector.calls == []


def test_retry_limit_reached_without_validating_or_executing(semantic, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("validated")

    monkeypatch.setattr(query, "validate_and_fix", boom)
    connector = Connector(FakeConn())
    state = {"temp:budget_deadline": 1060.0, "temp:sql_attempts": 3}
    out = _run(BY_COUNTY, semantic, connector, state=state)
    assert out["outcome"] == "query_rejected"
    assert "retry limit reached" in out["message"]
    assert state["temp:sql_attempts"] == 4
    assert connector.calls == []


def test_attempts_are_counted_and_third_attempt_is_allowed(semantic):
    state = {"temp:budget_deadline": 1060.0}
    conn = FakeConn()
    connector = Connector(conn)
    for _ in range(3):
        assert _run(BY_COUNTY, semantic, connector, state=state)["outcome"] == "answered"
    assert state["temp:sql_attempts"] == 3
    out = _run(BY_COUNTY, semantic, connector, state=state)
    assert out["outcome"] == "query_rejected" and len(conn.statements) == 3
    assert len(connector.calls) == 1  # one lazily opened, reused connection


def test_budget_exhausted_times_out_without_executing(semantic):
    clock = FakeClock(1000.0)
    connector = Connector(FakeConn())
    state = {"temp:budget_deadline": 1004.0}  # 4 s left < 5 s minimum
    out = _run(BY_COUNTY, semantic, connector, state=state, clock=clock)
    assert out["outcome"] == "timed_out"
    assert connector.calls == []


def test_missing_deadline_starts_a_fresh_budget(semantic):
    conn = FakeConn()
    out = _run(BY_COUNTY, semantic, Connector(conn), state={})
    assert out["outcome"] == "answered"
    assert any("STATEMENT_TIMEOUT_IN_SECONDS = 60" in s for s in conn.executed)


def test_statement_timeout_is_remaining_budget(semantic):
    conn = FakeConn()
    clock = FakeClock(1000.0)
    _run(BY_COUNTY, semantic, Connector(conn), state={"temp:budget_deadline": 1012.7}, clock=clock)
    assert any("STATEMENT_TIMEOUT_IN_SECONDS = 12" in s for s in conn.executed)


def test_timeout_cancels_by_query_id_and_never_resubmits(semantic):
    err = ProgrammingError(
        msg="Statement reached its statement or warehouse timeout of 60 second(s) and was canceled.",
        errno=630,
        sfqid="01b2-dead-beef",
    )
    conn = FakeConn(errors=[err])
    connector = Connector(conn)
    out = _run(BY_COUNTY, semantic, connector)
    assert out["outcome"] == "timed_out"
    assert out["query_id"] == "01b2-dead-beef"
    assert conn.cancelled == ["SELECT SYSTEM$CANCEL_QUERY('01b2-dead-beef')"]
    assert len(conn.statements) == 1
    assert len(connector.calls) == 1


def test_connection_error_before_start_reopens_and_retries_once(semantic):
    first = FakeConn(errors=[OperationalError(msg="connection reset", errno=250003)])
    second = FakeConn()
    connector = Connector(first, second)
    out = _run(BY_COUNTY, semantic, connector)
    assert out["outcome"] == "answered"
    assert len(connector.calls) == 2 and first.closed
    assert len(first.statements) == 1 and len(second.statements) == 1


def test_connection_error_twice_is_database_failure(semantic):
    first = FakeConn(errors=[OperationalError(msg="connection reset", errno=250003)])
    second = FakeConn(errors=[OperationalError(msg="connection reset", errno=250003)])
    connector = Connector(first, second)
    out = _run(BY_COUNTY, semantic, connector)
    assert out["outcome"] == "database_failure"
    assert len(connector.calls) == 2
    assert len(first.statements) + len(second.statements) == 2


def test_connection_error_after_start_is_database_failure_without_resubmit(semantic):
    err = OperationalError(msg="connection reset", errno=250003, sfqid="01b2-0000-0001")
    conn = FakeConn(errors=[err])
    connector = Connector(conn, FakeConn())
    out = _run(BY_COUNTY, semantic, connector)
    assert out["outcome"] == "database_failure"
    assert len(connector.calls) == 1 and len(conn.statements) == 1


def test_login_failure_is_generic_database_failure(semantic):
    settings = Settings()
    login = ProgrammingError(
        msg=f"Incorrect username or password was specified for {settings.snowflake_user} "
        f"password={PASSWORD} at https://{settings.snowflake_account}.snowflakecomputing.com",
        errno=250001,
    )
    out = _run(BY_COUNTY, semantic, Connector(login), settings=settings)
    assert out["outcome"] == "database_failure"
    text = json.dumps(out)
    for secret in (PASSWORD, settings.snowflake_account, "snowflakecomputing", "Traceback"):
        assert secret not in text


def test_missing_keychain_password_is_database_failure(semantic):
    out = run_query(
        BY_COUNTY,
        {},
        settings=Settings(),
        semantic_dir=semantic,
        connect=Connector(FakeConn()),
        get_password=lambda a, u: None,
        clock=FakeClock(),
    )
    assert out["outcome"] == "database_failure"


def test_sql_error_from_snowflake_is_rejected_for_retry(semantic):
    err = ProgrammingError(
        msg="SQL compilation error: division by zero type mismatch",
        errno=2016,
        sfqid="01b2-0000-0002",
        sqlstate="22000",
    )
    connector = Connector(FakeConn(errors=[err]))
    out = _run(BY_COUNTY, semantic, connector)
    assert out["outcome"] == "query_rejected"
    assert "SQL compilation error" in out["message"]


def test_truncated_result_states_the_cut(semantic):
    settings = Settings(max_result_rows=3)
    conn = FakeConn(responder=lambda cols: _default_rows(cols) * 3)  # 6 rows > 3
    out = _run(BY_COUNTY, semantic, Connector(conn), settings=settings)
    assert out["outcome"] == "truncated" and out["truncated"] is True
    assert len(out["rows"]) == 3
    assert {c["row"] for c in out["completeness"]} == {0, 1, 2}
    assert "3" in out["message"] and "truncat" in out["message"].lower()
    # validation limit is one above the cap, so a cut is detectable
    assert conn.statements[0].rstrip().endswith("LIMIT 4")


def test_metadata_is_cached_per_semantic_dir(semantic, monkeypatch):
    calls = []
    real = query.load_builder_metadata

    def counting(path, year):
        calls.append(year)
        return real(path, year)

    monkeypatch.setattr(query, "load_builder_metadata", counting)
    connector = Connector(FakeConn())
    _run(BY_COUNTY, semantic, connector, state={})
    _run(BY_COUNTY, semantic, connector, state={})
    assert sorted(calls) == [2019, 2020]
