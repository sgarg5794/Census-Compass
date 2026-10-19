import json
from pathlib import Path

from census_compass.contracts import load_geography
from census_compass.semantic.sources import load_census_api_labels, load_topics

SOURCE = Path("definitions/source")


def test_topics_parse_and_exclude_tables():
    topics = load_topics(SOURCE / "topics.yaml")
    assert len(topics) == 49 and sum(t.internal for t in topics) == 2
    assert not any(t.table.endswith(("B99", "PATTERNS")) for t in topics)


def test_census_api_label_mapping(tmp_path):
    (tmp_path / "2020_B01002E.json").write_text(json.dumps({"variables": {
        "B01002E_002E": {"label": "Estimate!!Median age --!!Male"},
        "B01002E_002M": {"label": "Margin of Error!!Median age --!!Male"},
        "B01002E_002EA": {"label": "Annotation of Estimate!!x"},
        "B01002E_002MA": {"label": "Annotation of Margin!!x"},
        "GEO_ID": {"label": "Geography"},
    }}))
    assert load_census_api_labels(tmp_path, 2020) == {"B01002Ee2": "Median age --!!Male"}


def test_geography_counts():
    g = load_geography(SOURCE, 2020)
    assert len(g) == 3234 and sum(c.block_groups == 0 for c in g) == 13
    assert sum(c.block_groups for c in g) == 242335
    g19 = load_geography(SOURCE, 2019)
    assert len(g19) == 3233 and sum(c.block_groups for c in g19) == 220333


def test_territory_state_abbreviations():
    for year in (2019, 2020):
        assert load_geography(SOURCE, year)
    g = load_geography(SOURCE, 2020)
    zero = [c for c in g if c.block_groups == 0]
    assert len(zero) == 13
    assert {c.state_abbr for c in zero} <= {"AS", "GU", "MP", "VI"}
