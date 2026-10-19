"""Build the semantic catalog and missing-definition reports from a saved snapshot.

Run: python -m scripts.build_catalog
"""

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from jsonschema import Draft202012Validator

from census_compass.semantic.catalog import build_catalog, write_table_schemas
from census_compass.semantic.definitions import convert_catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=Path("data/metadata/snapshot.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/tables"))
    parser.add_argument("--reports", type=Path, default=Path("data/tables"))
    parser.add_argument("--join-audit", type=Path, default=Path("data/metadata/join_audit.csv"))
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text())
    catalog, missing = build_catalog(snapshot)
    schema = json.loads(Path("data/tables/catalog.schema.json").read_text())
    Draft202012Validator(schema).validate(catalog)
    columns = [field for table in catalog["tables"].values() for field in table["columns"].values()]
    assert len(columns) == len(snapshot["columns"]), "Column inventory was not preserved"
    summary = {
        "extracted_at": snapshot["extracted_at"], "table_count": len(catalog["tables"]),
        "column_count": len(columns),
        "documented_columns": sum(field["metadata_status"] != "missing" for field in columns),
        "missing_columns": len(missing),
        "missing_by_reason": dict(sorted(Counter(row["reason"] for row in missing).items())),
        "missing_by_table": dict(sorted(Counter(row["table_name"] for row in missing).items())),
        "table_coverage": [
            {"table_name": name, "object_type": table["object_type"], "family": table["family"],
             "column_count": table["column_count"],
             "documented_columns": table["documented_column_count"],
             "missing_columns": table["column_count"] - table["documented_column_count"],
             "description_source": table["description_source"]}
            for name, table in catalog["tables"].items()
        ],
    }
    audit = []
    if args.join_audit.exists():
        with args.join_audit.open() as source:
            audit = list(csv.DictReader(source))
    converted = convert_catalog(catalog, audit)
    table_schema = json.loads(Path("data/tables/table.schema.json").read_text())
    validator = Draft202012Validator(table_schema)
    for definition in converted["tables"].values():
        validator.validate(definition)
    written = write_table_schemas(converted, args.output_dir)
    summary["semantic_format"] = "v1_dimensions_measures"
    summary["dimensions"] = sum(len(t["dimensions"]) for t in converted["tables"].values())
    summary["measures"] = sum(len(t["measures"]) for t in converted["tables"].values())
    summary["paired_margins_of_error"] = sum("margin_of_error" in m for t in converted["tables"].values() for m in t["measures"].values())
    summary["unresolved_columns"] = sum(len(t["unresolved_columns"]) for t in converted["tables"].values())
    summary["directed_relationships"] = sum(len(t["relationships"]) for t in converted["tables"].values())
    summary["table_schema_files"] = len(written)
    args.reports.mkdir(parents=True, exist_ok=True)
    header = ["table_name", "column_name", "ordinal_position", "data_type", "family",
              "is_identifier", "reason", "expected_dictionary"]
    with (args.reports / "missing_column_metadata.csv").open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=header)
        writer.writeheader()
        writer.writerows(missing)
    (args.reports / "catalog_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    grouped = defaultdict(list)
    for row in missing:
        grouped[row["table_name"]].append(row)
    lines = [
        "# Columns without supplied metadata", "",
        "Generated from the live schema and same-year Census field dictionaries.", "",
        f"- Tables/views: {summary['table_count']}",
        f"- Columns: {summary['column_count']}",
        f"- Columns with source definitions: {summary['documented_columns']}",
        f"- Columns without source definitions: {summary['missing_columns']}", "",
        "Missing metadata is not missing data. Identifier and supporting-table columns",
        "are listed separately from demographic fields absent from their dictionaries.", "",
        "## Reasons", "", "| Reason | Columns |", "| --- | ---: |",
    ]
    lines.extend(f"| {reason} | {count} |" for reason, count in summary["missing_by_reason"].items())
    lines.extend(["", "## Table coverage", "", "| Table/view | Columns | Defined | Missing |", "| --- | ---: | ---: | ---: |"])
    lines.extend(
        f"| `{row['table_name']}` | {row['column_count']} | {row['documented_columns']} | {row['missing_columns']} |"
        for row in summary["table_coverage"]
    )
    lines.extend(["", "## Complete missing-column list", ""])
    for name, rows in grouped.items():
        lines.extend([f"### {name}", "", "| Column | Type | Reason |", "| --- | --- | --- |"])
        lines.extend(f"| `{row['column_name']}` | {row['data_type']} | {row['reason']} |" for row in rows)
        lines.append("")
    (args.reports / "missing_column_metadata.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({key: value for key, value in summary.items() if key not in ("missing_by_table", "table_coverage")}, indent=2))


if __name__ == "__main__":
    main()
