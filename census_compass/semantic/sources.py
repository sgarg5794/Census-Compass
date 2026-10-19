"""Loaders for the builder's source inputs (topics mapping, Census API labels)."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_ESTIMATE_VARIABLE = re.compile(r"^([A-Z0-9]+)_(\d{3})E$")
_ESTIMATE_PREFIX = "Estimate!!"


@dataclass(frozen=True)
class TopicSpec:
    name: str
    title: str
    table: str
    groups: list[str]
    internal: bool = False


def load_topics(path: Path) -> list[TopicSpec]:
    entries = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return [
        TopicSpec(
            name=entry["name"],
            title=entry["title"],
            table=entry["table"],
            groups=list(entry.get("groups") or []),
            internal=bool(entry.get("internal", False)),
        )
        for entry in entries
    ]


def load_census_api_labels(directory: Path, year: int) -> dict[str, str]:
    """Map `<GROUP>e<n>` columns to Census API labels (estimates only, without `Estimate!!`)."""
    labels: dict[str, str] = {}
    for path in sorted(Path(directory).glob(f"{year}_*.json")):
        variables = json.loads(path.read_text(encoding="utf-8")).get("variables", {})
        for variable, info in variables.items():
            match = _ESTIMATE_VARIABLE.match(variable)
            if not match:
                continue
            label = info["label"]
            label = label.removeprefix(_ESTIMATE_PREFIX)
            labels[f"{match.group(1)}e{int(match.group(2))}"] = label
    return labels
