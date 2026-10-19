import pytest

from census_compass.config import Settings
from census_compass.database.connection import cancel, open_connection, run_statement


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.sfqid = "01b2-abc"
        self.description = [("A",), ("B",)]

    def execute(self, sql):
        self.conn.executed.append(sql)
        return self

    def fetchone(self):
        return (self.conn.role,)

    def fetchmany(self, n):
        self.conn.fetch_n = n
        return [(i, i) for i in range(self.conn.nrows)][:n]

    def close(self):
        pass


class FakeConn:
    def __init__(self, role="CENSUS_READER", nrows=0):
        self.role, self.nrows = role, nrows
        self.executed, self.closed, self.kwargs = [], False, None

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


def _open(role="CENSUS_READER"):
    conn = FakeConn(role)

    def connect(**kw):
        conn.kwargs = kw
        return conn

    s = Settings()
    return conn, open_connection(s, connect=connect, get_password=lambda a, u: "pw"), s


def test_secondary_roles_disabled_and_role_ok():
    conn, got, s = _open()
    assert got is conn
    assert "USE SECONDARY ROLES NONE" in conn.executed
    assert conn.kwargs["role"] == s.snowflake_role
    assert conn.kwargs["session_parameters"] == {"QUERY_TAG": "census_compass_agent"}


def test_wrong_role_raises_and_closes():
    conn = FakeConn(role="ACCOUNTADMIN")
    with pytest.raises(RuntimeError) as e:
        open_connection(Settings(), connect=lambda **kw: conn, get_password=lambda a, u: "pw")
    assert conn.closed
    assert "pw" not in str(e.value)


def test_missing_password():
    with pytest.raises(RuntimeError, match="--setup"):
        open_connection(Settings(), connect=lambda **kw: None, get_password=lambda a, u: None)


def test_run_statement_truncates():
    conn = FakeConn(nrows=50)
    cols, rows, truncated, qid = run_statement(conn, "SELECT 1", 30, 10)
    assert cols == ["A", "B"] and len(rows) == 10 and truncated and qid == "01b2-abc"
    assert conn.fetch_n == 11
    assert conn.executed[0] == "ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 30"


def test_run_statement_not_truncated():
    _, rows, truncated, _ = run_statement(FakeConn(nrows=10), "SELECT 1", 30, 10)
    assert len(rows) == 10 and not truncated


def test_cancel():
    conn = FakeConn()
    cancel(conn, "01b2-abc")
    assert conn.executed == ["SELECT SYSTEM$CANCEL_QUERY('01b2-abc')"]


def test_cancel_rejects_bad_id():
    with pytest.raises(ValueError):
        cancel(FakeConn(), "x'); DROP")
