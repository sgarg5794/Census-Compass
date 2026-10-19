"""Create traceable semantic definitions from an extracted Snowflake snapshot."""

import json
import re
from collections import defaultdict
from pathlib import Path

import yaml

IDENTIFIERS = {
    "CENSUS_BLOCK_GROUP", "STATE_FIPS", "COUNTY_FIPS", "TRACT_CODE", "BLOCK_GROUP",
    "TABLE_ID", "TABLE_NUMBER", "COLUMN_ID",
}
LEVEL_NAMES = [f"FIELD_LEVEL_{level}" for level in range(1, 11)]
LEVEL_NAMES[8] = "FIELD_LEVELl_9"  # Exact spelling supplied by the source database.


def quote_identifier(value):
    return '\"' + value.replace('\"', '\"\"') + '\"'


def qualified(database, schema, name):
    return ".".join(quote_identifier(part) for part in (database, schema, name))


def family(table):
    if re.fullmatch(r"(?:2019|2020)_CBG_[BC]\d+", table):
        return "demographic"
    if table == "2020_REDISTRICTING_CBG_DATA":
        return "redistricting"
    if table.endswith("_FIELD_DESCRIPTIONS"):
        return "field_dictionary"
    if table.endswith("_FIPS_CODES"):
        return "geographic_lookup"
    if "GEOMETRY" in table:
        return "geometry"
    if table.endswith("_GEOGRAPHIC_DATA"):
        return "geographic_attributes"
    return "supporting_data"


def dictionary_index(rows, field, name):
    result = {}
    for row in rows:
        code = row.get(field)
        if not code:
            raise ValueError(f"Missing {field} in {name}")
        if code in result:
            raise ValueError(f"Duplicate {field} {code} in {name}")
        result[code] = row
    return result


def describe_demographic(row):
    path = [row[name] for name in LEVEL_NAMES if row.get(name)]
    sentences = [str(row.get("FIELD_LEVEL_1") or "Statistic"), row.get("TABLE_TITLE")]
    if row.get("TABLE_UNIVERSE"):
        sentences.append("Universe: " + row["TABLE_UNIVERSE"])
    breakdown = [row[name] for name in LEVEL_NAMES[3:] if row.get(name)]
    if breakdown:
        sentences.append("Breakdown: " + " > ".join(breakdown))
    return ". ".join(part.rstrip(".") for part in sentences if part) + ".", path


def describe_redistricting(row):
    parts = [row.get("FIELD_NAME")]
    if row.get("COLUMN_TOPIC"):
        parts.append("Topic: " + row["COLUMN_TOPIC"])
    if row.get("COLUMN_UNIVERSE"):
        parts.append("Universe: " + row["COLUMN_UNIVERSE"])
    return ". ".join(part.rstrip(".") for part in parts if part) + "."


def build_catalog(snapshot):
    database, schema = snapshot["database"], snapshot["schema"]
    dictionaries = snapshot["dictionaries"]
    indexes = {
        name: dictionary_index(rows, "COLUMN_ID" if "REDISTRICTING" in name else "TABLE_ID", name)
        for name, rows in dictionaries.items()
    }
    grouped = defaultdict(list)
    for column in snapshot["columns"]:
        grouped[column["TABLE_NAME"]].append(column)
    catalog = {
        "version": 1,
        "source": {
            "database": database, "schema": schema, "extracted_at": snapshot["extracted_at"],
            "description_policy": "Source dictionary or column comment; no invented field meanings.",
        },
        "tables": {},
    }
    missing = []
    for table in snapshot["tables"]:
        name = table["TABLE_NAME"]
        table_family = family(name)
        year = int(name[:4]) if name[:4].isdigit() else None
        dictionary = None
        if table_family == "demographic":
            dictionary = f"{year}_METADATA_CBG_FIELD_DESCRIPTIONS"
        elif table_family == "redistricting":
            dictionary = "2020_REDISTRICTING_METADATA_CBG_FIELD_DESCRIPTIONS"
        definition = {
            "name": name,
            "display_name": name.replace("_", " "),
            "source_table": qualified(database, schema, name),
            "object_type": table["TABLE_TYPE"],
            "family": table_family,
            "year_label": year,
            "reporting_period": None,
            "description": None,
            "description_source": "schema_structure",
            "source_comment": table.get("COMMENT"),
            "topics": [],
            "census_table_groups": [],
            "grain": {"description": None, "verification_status": "not_inferred_from_metadata"},
            "relationships": [],
            "columns": {},
        }
        topics = set()
        groups = {}
        for column in sorted(grouped[name], key=lambda item: int(item["ORDINAL_POSITION"])):
            code = column["COLUMN_NAME"]
            row = indexes.get(dictionary, {}).get(code)
            is_identifier = code in IDENTIFIERS
            field = {
                "name": code, "ordinal_position": int(column["ORDINAL_POSITION"]),
                "data_type": column["DATA_TYPE"], "nullable": column["IS_NULLABLE"] == "YES",
                "sql": quote_identifier(code), "is_identifier": is_identifier,
                "semantic_type": "dimension" if is_identifier else "unclassified",
                "statistic_kind": None, "description": None, "metadata_status": "missing",
                "metadata_source": None, "source_metadata": None, "field_path": [],
                "universe": None, "aggregation": None, "embedding_eligible": False,
            }
            if row is not None:
                field["source_metadata"] = dict(row)
                field["metadata_status"] = "available"
                field["metadata_source"] = {
                    "table": qualified(database, schema, dictionary),
                    "match_column": "COLUMN_ID" if table_family == "redistricting" else "TABLE_ID",
                    "match_value": code,
                }
                field["semantic_type"] = "measure"
                if table_family == "demographic":
                    field["description"], field["field_path"] = describe_demographic(row)
                    field["universe"] = row.get("TABLE_UNIVERSE")
                    field["statistic_kind"] = {
                        "Estimate": "estimate", "MarginOfError": "margin_of_error",
                    }.get(row.get("FIELD_LEVEL_1"), "unspecified")
                    topics.update(t.strip() for t in (row.get("TABLE_TOPICS") or "").split(",") if t.strip())
                    groups[row["TABLE_NUMBER"]] = row.get("TABLE_TITLE")
                else:
                    field["description"] = describe_redistricting(row)
                    field["universe"] = row.get("COLUMN_UNIVERSE")
                    field["statistic_kind"] = "published_statistic"
                    if row.get("COLUMN_TOPIC"):
                        topics.add(row["COLUMN_TOPIC"])
                field["embedding_eligible"] = not is_identifier
            elif column.get("COMMENT") and column["COMMENT"].strip():
                field["description"] = column["COMMENT"].strip()
                field["metadata_status"] = "column_comment"
                field["metadata_source"] = {
                    "table": qualified(database, "INFORMATION_SCHEMA", "COLUMNS"),
                    "match_column": "COMMENT", "match_value": name + "." + code,
                }
            else:
                if is_identifier:
                    reason = "identifier_not_documented_by_field_dictionary"
                elif dictionary:
                    reason = f"missing_{table_family}_dictionary_entry"
                else:
                    reason = "no_field_dictionary_for_table"
                missing.append({
                    "table_name": name, "column_name": code,
                    "ordinal_position": field["ordinal_position"],
                    "data_type": field["data_type"], "family": table_family,
                    "is_identifier": is_identifier, "reason": reason,
                    "expected_dictionary": dictionary or "",
                })
            definition["columns"][code] = field
        definition["topics"] = sorted(topics)
        definition["census_table_groups"] = [
            {"code": code, "title": title} for code, title in sorted(groups.items())
        ]
        definition["column_count"] = len(definition["columns"])
        definition["documented_column_count"] = sum(
            field["metadata_status"] != "missing" for field in definition["columns"].values()
        )
        if table.get("COMMENT"):
            definition["description"] = table["COMMENT"]
            definition["description_source"] = "table_comment"
        elif groups:
            definition["description"] = (
                f"{year}-labeled Census demographic fields in {name}. "
                + "Contains Census statistical groups: "
                + "; ".join(f"{code}: {title}" for code, title in sorted(groups.items())) + "."
            )
            definition["description_source"] = "field_dictionary_summary"
        elif topics:
            definition["description"] = (
                f"{year}-labeled Census redistricting fields in {name}. "
                + "Topics: " + "; ".join(sorted(topics)) + "."
            )
            definition["description_source"] = "field_dictionary_summary"
        else:
            definition["description"] = (
                f"{table['TABLE_TYPE']} {name}, classified by its schema name as "
                f"{table_family}. Contains {definition['column_count']} columns. "
                "No table-level semantic definition was supplied; column names alone are not verified meanings."
            )
        catalog["tables"][name] = definition
    return catalog, missing


def table_schema_path(table_name, directory):
    if not isinstance(table_name, str) or not re.fullmatch(r"[A-Za-z0-9_]+", table_name):
        raise ValueError("Invalid table name; use an exact catalog table name")
    return Path(directory) / (table_name + ".yaml")


def write_table_schemas(catalog, directory):
    """Persist one self-contained document per table, not a combined YAML file."""
    documents = []
    for name, definition in catalog["tables"].items():
        path = table_schema_path(name, directory)
        if definition["name"] != name and definition.get("metadata", {}).get("physical_table") != name:
            raise ValueError("Table identity does not match catalog key")
        document = dict(definition) if "dimensions" in definition else {
            "version": catalog["version"], "source": catalog["source"], **definition
        }
        documents.append((path, document))
    Path(directory).mkdir(parents=True, exist_ok=True)
    for path, document in documents:
        temporary = path.with_suffix(".yaml.tmp")
        with temporary.open("w") as output:
            yaml.safe_dump(document, output, sort_keys=False, allow_unicode=True, width=110)
        temporary.replace(path)
    index = {}
    for path, document in documents:
        for alias in (path.stem, document["name"]):
            if alias in index and index[alias] != path.name:
                raise ValueError("Duplicate schema lookup alias")
            index[alias] = path.name
    index_path = Path(directory) / "table_index.json"
    temporary = index_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(index, indent=2) + "\n")
    temporary.replace(index_path)
    return [path for path, _ in documents]


def load_table_schema(table_name, directory=Path("data/tables")):
    """Load only the selected table; underlying lookup for the planned get_schema tool."""
    path = table_schema_path(table_name, directory)
    if not path.exists():
        index_path = Path(directory) / "table_index.json"
        if index_path.exists():
            index = json.loads(index_path.read_text())
            filename = index.get(table_name)
            if filename is not None:
                if not isinstance(filename, str) or not filename.endswith(".yaml"):
                    raise ValueError("Invalid table name in schema lookup index")
                path = table_schema_path(filename[:-5], directory)
    with path.open() as source:
        document = yaml.safe_load(source)
    if not isinstance(document, dict):
        raise ValueError("Schema identity does not match requested table")
    identities = (document.get("name"), document.get("metadata", {}).get("physical_table"))
    if table_name not in identities:
        raise ValueError("Schema identity does not match requested table")
    return document
