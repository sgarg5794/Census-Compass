# Source-derived semantic catalog extraction

Scope explicitly requested: create definitions for all tables using existing
metadata and enumerate columns without metadata. Execute inline in the requested
project, preserving existing uncommitted scaffold files and credentials.

- [x] Add tests for year isolation, hierarchy/provenance, missing definitions,
  redistricting, duplicate dictionary IDs, and complete schema preservation.
  Initial run: all 8 failed because the builder was not implemented.
- [x] Extract live schemas, comments, and all three dictionaries via local Keychain.
- [x] Generate one catalog with exact source fields; leave unsupported semantics unset.
- [x] Produce CSV/Markdown missing-column reports and JSON coverage summary.
- [x] Validate generated catalog, full test suite, and lint; inspect actual examples.

Do not create embeddings, implement ADK tools, infer aggregation rules, or invent
geographic/supporting-field descriptions. Structural table summaries are labeled
as structural rather than externally verified semantic definitions.

## Results
- Outputs moved to data/tables/ at the user request.
- Live extraction: 73 tables/views, 17,052 columns, 3 dictionaries.
- Defined columns: 16,582. Missing definitions: 470 (296 demographic, 89 identifiers, 85 supporting fields).
- Table descriptions: 59 dictionary summaries, 1 supplied table comment, 13 explicitly structural summaries.
- Full test suite: 8 passed. Ruff and git diff whitespace checks passed.
- Saved YAML passed JSON Schema validation and exact inventory comparison against the snapshot.
- Missing CSV exactly matched unresolved catalog fields. No aggregation rules were inferred.
- Author self-review: checked source-year isolation, exact identifiers, full field-level provenance, missing-field handling, and preservation of all tables and columns.

## User-requested per-table layout
- Supersedes the original single-catalog file: one YAML per physical table/view.
- Updated build command and config to use data/tables/ as a directory.
- Added load_table_schema for selective lookup without reading unrelated schemas.
- 7 new tests failed before implementation; all 15 tests now pass.
- Validated all 73 individual YAML documents against table.schema.json.
- All 17,052 source columns and all 470 missing-definition entries preserved.
- Removed legacy catalog.yaml only after every replacement passed validation.
- ADK get_schema registration remains future work; this adds its underlying loader.
