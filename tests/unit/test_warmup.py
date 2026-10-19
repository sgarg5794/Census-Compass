import census_compass.tools.schema as schema
from census_compass import warmup


def _reset(monkeypatch):
    monkeypatch.setattr(warmup, "_started", False)


def test_warm_up_runs_one_search_once(monkeypatch):
    _reset(monkeypatch)
    calls = []
    monkeypatch.setattr(schema, "search_schema", lambda q, y: calls.append((q, y)) or {"results": []})
    thread = warmup.start_warm_up()
    thread.join(timeout=5)
    assert calls == [("population", 2020)]
    assert warmup.start_warm_up() is None  # a second call does nothing
    assert calls == [("population", 2020)]


def test_warm_up_failure_is_swallowed(monkeypatch, caplog):
    _reset(monkeypatch)

    def boom(q, y):
        raise RuntimeError("secret-detail")

    monkeypatch.setattr(schema, "search_schema", boom)
    thread = warmup.start_warm_up()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert "secret-detail" not in caplog.text


def test_overlapping_first_searches_take_turns(monkeypatch):
    import threading
    import time

    monkeypatch.setattr(schema, "_warmed", False)
    active, peak = [0], [0]
    guard = threading.Lock()

    def slow_search(query, year, population_group):
        with guard:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.2)
        with guard:
            active[0] -= 1
        return {"results": []}

    monkeypatch.setattr(schema, "_search_schema", slow_search)
    threads = [threading.Thread(target=schema.search_schema, args=("q", 2020)) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert peak[0] == 1  # never two cold searches at once
    assert schema._warmed is True
