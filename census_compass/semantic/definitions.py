"""Convert source-derived fields into the approved v1 per-table schema."""

import re
from collections import Counter

TABLE_NAMES = {
    "B01": "population_age_and_sex", "B02": "race", "B03": "hispanic_latino_origin",
    "B07": "geographic_mobility", "B08": "work_location_and_commuting",
    "B09": "children_and_household_relationships", "B11": "households_and_families",
    "B12": "marital_status", "B14": "school_enrollment", "B15": "educational_attainment",
    "B16": "language_and_english_proficiency", "B17": "poverty", "B19": "household_income",
    "B20": "earnings", "B21": "veteran_status", "B22": "snap_and_disability",
    "B23": "employment_and_labor_force", "B24": "class_of_worker", "B25": "housing",
    "B27": "health_insurance", "B28": "computers_and_internet",
    "B29": "citizen_voting_age_population", "B99": "response_allocation",
    "C02": "detailed_race", "C15": "bachelors_degree_fields", "C16": "household_language",
    "C17": "income_to_poverty_ratio", "C21": "veterans_poverty_and_disability",
    "C24": "occupation",
}
KEY_DESCRIPTIONS = {
    "CENSUS_BLOCK_GROUP": "Census block-group geographic identifier for the area summarized by this row.",
    "STATE_FIPS": "State geographic code; preserve leading zeros.",
    "COUNTY_FIPS": "County geographic code used together with STATE_FIPS to identify a county.",
    "TRACT_CODE": "Source Census tract identifier within its state and county.",
    "BLOCK_GROUP": "Source block-group code within its Census tract.",
    "TABLE_ID": "Demographic field code described by this dictionary row; not a physical table name.",
    "TABLE_NUMBER": "Census statistical table/group code associated with the field definition.",
    "COLUMN_ID": "Redistricting field code described by this dictionary row.",
}


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def model_name(table):
    parts = table.split("_")
    year = parts.pop(0)
    if len(parts) == 2 and parts[0] == "CBG" and parts[1] in TABLE_NAMES:
        return TABLE_NAMES[parts[1]] + "_" + year
    return "_".join(parts).lower() + "_" + year


def measure_name(code, field):
    if code == "B01001e1":
        return "population"
    if code == "B01003e1":
        return "total_population_b01003"
    path = [part for part in field.get("field_path", [])[3:] if part != "Total"]
    if code.startswith("B01001e"):
        return slug("population " + " ".join(path))
    metadata = field.get("source_metadata") or {}
    title = metadata.get("TABLE_TITLE") or metadata.get("FIELD_NAME") or code
    if code.startswith("B01002"):
        return slug(title + " " + " ".join(p for p in path if p != "Median age"))
    return slug(title + " " + " ".join(path))[:150] + "_" + code.lower()


def numeric_type(source_type):
    return {"TEXT": "string", "FLOAT": "number", "NUMBER": "number", "BOOLEAN": "boolean",
            "DATE": "date", "VARIANT": "object", "GEOGRAPHY": "geography"}.get(source_type, "unknown")


def count_operations():
    return {"allowed": {
        "sum": {"meaning": "Total for this population category across selected block groups."},
        "avg": {"meaning": "Average category count per selected block group; not average age."},
        "min": {"meaning": "Smallest block-group category estimate among selected block groups."},
        "max": {"meaning": "Largest block-group category estimate among selected block groups."},
    }}


def physical_columns(document):
    """Return source coverage, failing if a column appears more than once."""
    codes = list(document["unresolved_columns"])
    for section in ("dimensions", "measures"):
        for field in document[section].values():
            codes.append(field["metadata"]["source_column"])
            if "margin_of_error" in field:
                codes.append(field["margin_of_error"]["metadata"]["source_column"])
    repeated = [code for code, count in Counter(codes).items() if count > 1]
    if repeated:
        raise ValueError("Duplicate source columns: " + ", ".join(repeated))
    return set(codes)


def field_metadata(code, field):
    return {
        "source_column": code, "metadata_status": field["metadata_status"],
        "statistic_kind": field.get("statistic_kind"),
        "field_path": field.get("field_path", []),
        "definition_source": field.get("metadata_source"),
        "source_metadata": field.get("source_metadata"),
        "nullable": field.get("nullable", True),
    }


def make_measure(code, field):
    source = field.get("source_metadata") or {}
    title = source.get("TABLE_TITLE", "")
    median = "median" in title.lower()
    population_count = bool(re.fullmatch(r"B01001e\d+|B01003e1", code))
    population_median = code.startswith("B01002") and median
    metadata = field_metadata(code, field)
    metadata.update({
        "embedding_eligible": field.get("statistic_kind") != "margin_of_error",
        "aggregation_status": "approved_population_count_operations" if population_count else "requires_measure_specific_review",
    })
    result = {
        "description": field["description"], "data_type": numeric_type(field["data_type"]),
        "source_data_type": field["data_type"],
        "unit": "people" if population_count else ("years" if population_median else None),
        "universe": field.get("universe"), "sql": field["sql"],
        "aggregation": count_operations() if population_count else {"allowed": {}},
        "constraints": {}, "metadata": metadata,
    }
    if population_count:
        result["constraints"] = {
            "explanation": "Combine only this category across distinct non-overlapping block groups. Do not add overlapping totals and categories.",
        }
    elif median:
        result["constraints"] = {
            "allowed_result_grains": ["census_block_group"],
            "explanation": "Do not SUM, AVG, or weight published block-group medians to claim a combined county/state median. Individual values may be retrieved, filtered, compared, or sorted.",
        }
    else:
        result["constraints"] = {
            "aggregation_review_required": True,
            "explanation": "Source field meaning is documented; valid units and cross-row aggregations require measure-specific review. No aggregate operation is authorized yet.",
        }
    if field.get("statistic_kind") == "margin_of_error":
        result["aggregation"] = {"allowed": {}}
        result["constraints"] = {"explanation": "Unpaired margin of error: retain it for exact-field lookup; do not aggregate without a verified statistical method."}
    return result


def attach_relationships(tables, audit):
    for row in audit:
        a, b = row["TABLE_A"], row["TABLE_B"]
        if a not in tables or b not in tables:
            continue
        observed = row["OBSERVED_RELATIONSHIP"]
        forward = {
            "ONE_TO_ONE_ON_MATCHING_KEYS": "one_to_one",
            "MANY_A_TO_ONE_B_ON_MATCHING_KEYS": "many_to_one",
            "ONE_A_TO_MANY_B_ON_MATCHING_KEYS": "one_to_many",
        }.get(observed)
        if forward is None:
            continue
        keys = ["CENSUS_BLOCK_GROUP"] if row["JOIN_FAMILY"] == "CBG" else ["STATE_FIPS", "COUNTY_FIPS"]
        for name, target, side, other in ((a, b, "A", "B"), (b, a, "B", "A")):
            source = tables[name]
            if not all(key.lower() in source["dimensions"] for key in keys):
                raise ValueError("Audited relationship references a missing source dimension")
            if not all(key.lower() in tables[target]["dimensions"] for key in keys):
                raise ValueError("Audited relationship references a missing target dimension")
            cardinality = forward if side == "A" else {"many_to_one": "one_to_many", "one_to_many": "many_to_one", "one_to_one": "one_to_one"}[forward]
            source["relationships"][tables[target]["name"]] = {
                "target_table": target,
                "target_model": tables[target]["name"],
                "description": "Audited same-year join using " + " + ".join(keys) + ". Cardinality applies to matching non-null keys; inspect coverage before choosing a join type.",
                "join_on": [{"source_dimension": key.lower(), "target_column": key} for key in keys],
                "cardinality": cardinality,
                "metadata": {
                    "verification_status": "verified_by_database_query",
                    "coverage": {
                        "source_keys_without_target": int(row[side + "_KEYS_MISSING_FROM_" + other]),
                        "target_keys_without_source": int(row[other + "_KEYS_MISSING_FROM_" + side]),
                        "source_null_key_rows": int(row[side + "_NULL_KEYS"]),
                        "target_null_key_rows": int(row[other + "_NULL_KEYS"]),
                        "matching_distinct_keys": int(row["MATCHING_DISTINCT_KEYS"]),
                        "inner_join_rows": int(row["INNER_JOIN_ROWS"]),
                    },
                },
            }
            if int(row[side + "_DUPLICATE_KEY_ROWS"]) == 0:
                if int(row[side + "_NULL_KEYS"]) == 0:
                    source["grain"] = {
                        "description": "One row per " + ("Census block group." if row["JOIN_FAMILY"] == "CBG" else "state/county code pair."),
                        "primary_key": [key.lower() for key in keys],
                        "verification": "Unique non-null key confirmed in the earlier live join audit.",
                    }
                elif not source["grain"]["primary_key"]:
                    source["grain"]["verification"] = "Non-null keys are unique, but null identifiers exist; no complete primary key is asserted."


def convert_catalog(catalog, audit=()):
    converted = {"version": 1, "source": catalog["source"], "tables": {}}
    for physical, raw in catalog["tables"].items():
        columns = raw["columns"]
        document = {
            "version": 1, "name": model_name(physical), "description": raw["description"],
            "source_table": raw["source_table"],
            "grain": {"description": None, "primary_key": [], "verification": "Not established by source dictionaries alone."},
            "dimensions": {}, "measures": {}, "relationships": {},
            "constraints": [
                "Choose an allowed aggregation explicitly; there is no default.",
                "Direct retrieval and sorting do not themselves require aggregation.",
                "Do not treat NULL or unverified numeric sentinel codes as zero.",
                "Do not combine overlapping categories or duplicate records introduced by joins.",
                "Check geographic coverage, compatible universes, reporting periods, and uncertainty before combining values.",
            ],
            "unresolved_columns": {},
            "metadata": {
                "schema_status": "v1", "physical_table": physical,
                "object_type": raw["object_type"], "family": raw["family"],
                "year_label": raw.get("year_label"), "reporting_period": None,
                "description_source": raw.get("description_source", "source_dictionary"),
                "source_extracted_at": catalog["source"].get("extracted_at"),
                "physical_column_count": len(columns),
                "aggregation_policy": "Population count operations are approved. Other statistical concepts require individual review; empty allowed operations do not authorize rollup.",
                "source_dictionary_gap_count": sum(f["metadata_status"] == "missing" for f in columns.values()),
            },
        }
        consumed = set()
        for code, field in columns.items():
            if field.get("is_identifier"):
                metadata = field_metadata(code, field)
                metadata.update({"embedding_eligible": False, "description_source": "project_identifier_interpretation"})
                document["dimensions"][code.lower()] = {
                    "description": field.get("description") or KEY_DESCRIPTIONS.get(code),
                    "data_type": numeric_type(field["data_type"]), "source_data_type": field["data_type"],
                    "sql": field["sql"], "constraints": ["Preserve identifier formatting and leading zeros."],
                    "metadata": metadata,
                }
                consumed.add(code)
        for code, field in columns.items():
            if code in consumed:
                continue
            if field["metadata_status"] == "missing":
                document["unresolved_columns"][code] = {
                    "sql": field["sql"], "data_type": numeric_type(field["data_type"]),
                    "source_data_type": field["data_type"], "description": None,
                    "reason": "No supplied field-dictionary definition or column comment.",
                    "queryable": False, "embedding_eligible": False,
                }
                consumed.add(code)
                continue
            if field.get("statistic_kind") == "margin_of_error":
                match = re.fullmatch(r"(.+)m(\d+)", code)
                if match and match[1] + "e" + match[2] in columns:
                    continue
            measure = make_measure(code, field)
            key = measure_name(code, field)
            if key in document["measures"]:
                key += "_" + code.lower()
            if key in document["measures"]:
                raise ValueError("Duplicate semantic measure name: " + key)
            document["measures"][key] = measure
            consumed.add(code)
            match = re.fullmatch(r"(.+)e(\d+)", code)
            if match:
                moe_code = match[1] + "m" + match[2]
                moe = columns.get(moe_code)
                if moe is not None and moe["metadata_status"] != "missing":
                    measure["margin_of_error"] = {
                        "sql": moe["sql"], "description": moe["description"],
                        "data_type": numeric_type(moe["data_type"]), "source_data_type": moe["data_type"],
                        "unit": measure["unit"], "aggregation": {"allowed": {}},
                        "constraints": [
                            "Do not apply estimate aggregation rules to uncertainty.",
                            "Combined uncertainty requires a separately verified method; none is configured.",
                            "An available estimate does not guarantee an available margin of error.",
                        ],
                        "metadata": field_metadata(moe_code, moe),
                    }
                    consumed.add(moe_code)
        if physical_columns(document) != set(columns):
            raise ValueError("Physical column coverage changed for " + physical)
        if raw["family"] == "field_dictionary":
            key = "column_id" if "REDISTRICTING" in physical else "table_id"
            if key in document["dimensions"]:
                document["grain"] = {
                    "description": "One row per demographic field definition.",
                    "primary_key": [key],
                    "verification": "Dictionary codes were checked for uniqueness during catalog extraction.",
                }
        document["metadata"]["documented_measure_count"] = len(document["measures"])
        document["metadata"]["unresolved_column_count"] = len(document["unresolved_columns"])
        converted["tables"][physical] = document
    names = [table["name"] for table in converted["tables"].values()]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate logical table names")
    attach_relationships(converted["tables"], audit)
    return converted
