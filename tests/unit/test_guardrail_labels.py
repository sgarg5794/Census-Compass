"""Label set shape and evaluation-runner metric tests (no network)."""

from collections import Counter
from pathlib import Path
from typing import get_args

import yaml

from census_compass.guardrails.contracts import Decision, Label
from scripts import run_guardrail_eval as ev

LABELS_PATH = Path(__file__).resolve().parents[2] / "evaluations" / "guardrail_labels.yaml"


def _entries():
    return yaml.safe_load(LABELS_PATH.read_text(encoding="utf-8"))


def test_label_set_composition():
    entries = _entries()
    assert len(entries) >= 80
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids))
    assert all(e["expected"] in get_args(Label) for e in entries)
    counts = Counter(e["expected"] for e in entries)
    minimums = {
        "data": 20,
        "general": 10,
        "smalltalk": 6,
        "mixed": 6,
        "off_topic": 12,
        "inappropriate": 10,
        "manipulation": 8,
    }
    for label, minimum in minimums.items():
        assert counts[label] >= minimum, label
    tags = Counter(t for e in entries for t in e["tags"])
    assert tags["sensitive_legit"] >= 15
    assert tags["adversarial"] >= 3
    assert sum(1 for e in entries if e["history"]) >= 6
    for e in entries:
        assert set(e) == {"id", "text", "history", "expected", "tags"}
        assert all(t["role"] in {"user", "assistant"} and t["text"] for t in e["history"])


def test_no_duplicate_texts():
    keys = [(e["text"], str(e["history"])) for e in _entries()]
    assert len(keys) == len(set(keys))
    texts = [e["text"] for e in _entries() if not e["history"]]
    assert len(texts) == len(set(texts))


def test_sensitive_legit_are_expected_data():
    for e in _entries():
        if "sensitive_legit" in e["tags"]:
            assert e["expected"] == "data", e["id"]


def _r(id, expected, predicted, latency, tags=()):
    return {
        "id": id,
        "text": id,
        "expected": expected,
        "predicted": predicted,
        "latency": latency,
        "tags": list(tags),
        "neutral_offer": None,
    }


def test_compute_report_metrics():
    results = [
        _r("a", "data", "data", 1.0),
        _r("b", "data", "off_topic", 2.0, ["sensitive_legit"]),  # sensitive blocked + miss
        _r("c", "inappropriate", "inappropriate", 3.0),
        _r("d", "manipulation", "data", 4.0),  # blocked-label message not blocked
        _r("e", "general", None, 5.0),  # classifier failure: miss, never blocked
    ]
    rep = ev.compute_report(results)
    assert rep["sensitive_blocked"] == 1
    assert rep["accuracy"] == 2 / 5
    assert rep["blocked_recall"] == 1 / 2
    assert rep["p95_latency"] == 5.0
    assert [m["id"] for m in rep["misses"]] == ["b", "d", "e"]


def test_compute_report_p95_nearest_rank():
    results = [_r(str(i), "data", "data", float(i)) for i in range(1, 21)]
    assert ev.compute_report(results)["p95_latency"] == 19.0


def test_off_census_offer_warnings():
    assert ev.off_census_offers([{"id": "x", "neutral_offer": "What are crime rates by race?"}])
    assert not ev.off_census_offers(
        [{"id": "x", "neutral_offer": "What is the poverty rate by race?"}]
    )


def _patch(monkeypatch, tmp_path, label_for):
    async def fake(message, context, **kw):
        return label_for(message)

    monkeypatch.setattr(ev, "classify", fake)
    monkeypatch.setattr(ev, "REPORT_PATH", tmp_path / "report.json")
    monkeypatch.setattr(ev, "load_environment", lambda: None)


def test_main_exit_code_reflects_thresholds(monkeypatch, tmp_path):
    expected = {e["text"]: e["expected"] for e in _entries()}
    _patch(monkeypatch, tmp_path, lambda m: Decision(label=expected[m]))
    assert ev.main([]) == 0
    assert (tmp_path / "report.json").exists()

    _patch(monkeypatch, tmp_path, lambda m: Decision(label="off_topic"))
    assert ev.main([]) == 1

    _patch(monkeypatch, tmp_path, lambda m: None)
    assert ev.main([]) == 1


def test_main_limit(monkeypatch, tmp_path):
    seen = []

    def label_for(m):
        seen.append(m)
        return Decision(label="data")

    _patch(monkeypatch, tmp_path, label_for)
    ev.main(["--limit", "3"])
    assert len(seen) == 3
