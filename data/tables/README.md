# V1 per-table semantic definitions

There is one YAML file per physical table/view. Filenames retain exact Snowflake
names (`2020_CBG_B01.yaml`), while `name` inside each file is a meaningful model
name (`population_age_and_sex_2020`).

Every table follows the approved v1 layout:
- `version`, `name`, `description`, `source_table`.
- `grain`: description and verified primary key, or an explicit unresolved status.
- `dimensions`: identifiers, exact SQL expressions, source types, and provenance.
- `measures`: source-derived meanings, SQL expressions, explicit allowed aggregation
  operations, constraints, and metadata. Associated errors are nested under
  `margin_of_error`; they retain their own physical column IDs and definitions.
- `relationships`: verified same-year joins, cardinality, and missing-key coverage.
- `constraints`: table-level interpretation rules.
- `unresolved_columns`: fields without supplied meaning; query/embedding use disabled.
- `metadata`: physical identity, source status, year label, and unresolved decisions.

There is no default aggregation. Approved population-count fields allow explicit
SUM/AVG/MIN/MAX operations with defined meanings. These permissions are not applied
to other numeric fields automatically. Published medians prohibit geographic
rollups; other concepts have an empty allowed set until individually reviewed.
An empty allowed set still permits appropriate direct published-value retrieval.
Uncertainty never inherits its estimate's aggregation rules. Unit values remain
null when the source information does not establish them.

## Load one table
```python
from census_compass.semantic.catalog import load_table_schema

schema = load_table_schema("2020_CBG_B01")
same_schema = load_table_schema("population_age_and_sex_2020")
```

Only the selected YAML file is loaded. `table_index.json` is a small alias-to-file
lookup, not an embedding manifest. The ADK get_schema wrapper is still pending.

## Source coverage
All 17,052 source columns are represented exactly once: as a dimension, measure,
nested margin of error, or unresolved column. Source dictionaries define 16,582
columns. The 470 source-metadata gaps comprise 296 demographic fields, 89 identifier
columns, and 85 other supporting columns. Identifier descriptions may come from
explicit project interpretations and remain marked as missing a source-dictionary
entry; the source-gap reports therefore still include them.

`metadata_status: available` means a dictionary definition was found, not that
all geographic data values are populated or that every calculation is authorized.
Structural table descriptions and missing source definitions remain labeled.

## Support files
- `table.schema.json`: validates the approved v1 documents.
- `catalog.schema.json`: validates the builder's source-derived intermediate form.
- `table_index.json`: lookup by physical or logical table name.
- `missing_column_metadata.csv` and `.md`: unchanged source-gap reports.
- `catalog_summary.json`: source coverage and v1 document counts.

## Regenerate
```sh
.venv/bin/python -m scripts.extract_metadata
.venv/bin/python -m scripts.build_catalog
```

The raw extraction is ignored in `data/metadata/`. Audited joins are read from
`data/metadata/join_audit.csv`, copied from the earlier database audit. Refresh
that audit when the source geography or tables change; missing audits produce no
invented relationships. The generator validates every document before writing.
No embeddings or agent tools are executed by these commands.
