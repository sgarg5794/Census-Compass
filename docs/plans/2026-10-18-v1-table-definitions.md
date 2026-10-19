# Apply approved v1 table definitions

Spec: docs/examples/population_age_and_sex_2020.yaml, approved by the user.
Scope: update all 73 definitions, generator, loader, and validation. Execute inline.

- [x] Add seven regression tests; initially all failed on missing conversion behavior.
- [x] Convert fields to dimensions, measures with nested uncertainty, and unresolved columns.
- [x] Retain physical filenames; add logical names and a lightweight schema lookup index.
- [x] Preserve source descriptions/provenance and import validated join coverage.
- [x] Regenerate all table files and unchanged source-metadata gap reports.
- [x] Validate all saved files, exact physical coverage, relationships, tests and lint.

Population count aggregation follows the approved example. Other statistical
concepts require individual review; do not generalize SUM to all numeric fields.
Reporting periods are not inferred from year labels. No agent tools or embeddings
are implemented. Public deployment authentication remains deferred.

## Verification evidence
- Initial seven migration tests failed before implementation; full suite now 22 passed.
- All 73 saved v1 documents pass JSON Schema validation.
- All 17,052 source columns are represented exactly once; no SQL identifiers changed.
- 8,440 measures, 8,142 paired margins of error, 89 identifier dimensions,
  381 unresolved non-identifier columns, and 2,252 audited directional relationships.
- Original 470 source gaps preserved: identifiers can have explicit project-level
  meanings without inventing source-dictionary provenance.
- No default aggregation exists. Only approved population-count fields authorize
  SUM/AVG/MIN/MAX; other concepts require individual review.
- Primary keys and relationship endpoints resolve; logical/physical lookup agrees.
- Author self-review checked unit inference, unsafe rollups, null-key grain claims,
  column pairing, safe lookup, and preservation of audit coverage direction.
