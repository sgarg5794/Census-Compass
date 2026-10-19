# Demographic questions: capabilities and limits

Working notes as of October 18, 2026. This records our current understanding, not a completed validation of every measure. Update as documentation and data checks resolve open questions.

## Geographic support

| Geography | Current status |
| --- | --- |
| Census block group | Direct demographic records are available for 2019 and 2020. Confirm the chosen field definition, source period, and value availability. |
| County | Block groups connect through geometry to state/county FIPS codes. Same-year joins have been audited. Additive measures and correctly calculated ratios can be aggregated, subject to geographic coverage checks. |
| State | Block groups connect to state identifiers through geometry. Additive measures and correctly calculated ratios can be aggregated, subject to coverage checks. |
| Entire dataset / national totals | Totals across available records can be calculated. Do not call them complete US totals until included states, territories, and geographic coverage are independently verified. |
| ZIP code / ZCTA | Not currently supported by a verified mapping or direct ZCTA statistics in the inspected tables. Tract codes are not ZIP codes. Additional data and geographic-vintage validation are needed. ZCTAs approximate postal ZIP-code areas. |
| City / town / named neighborhood / custom region | No verified mapping established. County or tract names must not be substituted for these areas. Need appropriate boundaries, relationships, or direct statistics. |

## Question types

| Question type | Capability / conditions |
| --- | --- |
| Population or household count for a block group | Use an available, verified estimate/count field and identify its source and reporting period. |
| County/state total population, household counts, or category counts | Sum additive counts across unique, non-overlapping block groups in the requested geography. Verify coverage and missing values. Do not add overlapping categories. |
| Age, race/ethnicity, education, employment, housing, income categories, commuting, and similar breakdowns | Potentially supported where exact fields, category definitions, and population universes are available. Metadata lists these topics; not every conceivable breakdown or combination is available. |
| County/state percentages or rates | Sum the appropriate numerator and denominator separately, then divide. Use compatible universes and periods. Do not average block-group percentages equally. |
| Block-group median income or median age | Can report the published value if available and its definition is verified. Income reference-year dollars and uncertainty must be explained as relevant. |
| Exact county/state median income or median age | Cannot derive by averaging, weighting, or summing block-group medians. Need a directly published statistic or underlying information sufficient for a valid calculation. A grouped-distribution approximation would need a documented method and explicit labeling. |
| Rankings/comparisons within a year | Possible for supported measures and comparable geographies. Handle missing values and margins of error; a numerical difference alone does not establish statistical significance. |
| Statistical significance or uncertainty of an aggregate | Requires appropriate Census methods, compatible estimates, and available uncertainty information. Do not simply add margins of error or interpret them as guaranteed bounds. |
| Change between 2019 and 2020 | Not yet generally validated. Verify source periods, overlapping survey windows, geographic boundaries, field definitions, and income price bases. Matching IDs alone is insufficient. |
| Latest/current population or demographic forecasts | Not supported by the historical tables alone. Do not present 2019/2020 data as current observations or predictions. |
| Individual-level information or arbitrary combinations of characteristics | Aggregate tables do not establish facts about individuals or provide arbitrary cross-tabulations. For example, separate income and education totals cannot establish the income of college graduates. |
| Causal explanations | Descriptive aggregate statistics alone do not establish why a demographic change or difference occurred. |

## Verified joins and coverage exceptions

- All 33 tested 2020 block-group tables have the same 242,335 unique, non-null block-group IDs. All 528 pairwise block-group joins match completely.
- The 29 demographic tables for 2019, geographic metadata, and rent-percentage table share 220,333 unique block-group IDs.
- Both 2019 geometry tables cover all those demographic IDs and contain 407 additional IDs.
- Relative to 2019 demographics, the patterns table lacks two IDs, has 403 extra non-null IDs, and contains one null ID.
- The 2019 total-rental-geo table covers 162,721 demographic IDs, leaving 57,612 unmatched. Do not use it as the geographic base for all-population questions.
- All tested geometry rows match exactly one same-year FIPS lookup row on STATE_FIPS plus COUNTY_FIPS. Multiple block groups per county are expected. The 2020 FIPS lookup has 13 county keys without geometry matches.
- These checks establish key relationships and relative table coverage, not completeness against an external Census geographic universe or consistency of all geographic attributes.
- Cross-year joins, ZIP/ZCTA mappings, city mappings, and ID-prefix versus explicit-FIPS consistency have not been verified.

## Remaining interpretation checks

- Verify source and reporting period by table family. The official Census B01002E table for 2020 is a 2016–2020 ACS five-year table; do not generalize that finding to every table without verification. Redistricting and ACS tables must retain distinct source labels.
- The supplied metadata describes estimates and margins of error separately. Null does not mean zero; estimates and their margins of error may have different availability.
- Income field B19013e1 is labeled in 2019 dollars in the 2019 metadata and 2020 dollars in the 2020 metadata. Each is already expressed in its stated price basis; a purchasing-power comparison needs a common basis.
- 148 demographic columns per year lack supplied descriptions, across 11 uppercase-E groups. One group, B01002E, has been investigated; its expected field mapping is consistent with sample data but has not been independently matched to Census values for the same block groups. The full metadata gap remains unresolved.
- Profile nulls and any special numeric codes before calculations. Check geography completeness, units, universes, and aggregation rules for supported measures.

## Evidence reviewed

- Column inventory: `/Users/sahilgarg/Downloads/Untitled 2_2026-10-18-0346.csv`
- 2019 field descriptions: `/Users/sahilgarg/Downloads/Untitled 2_2026-10-18-0346 (1).csv`
- 2020 field descriptions: `/Users/sahilgarg/Downloads/Untitled 2_2026-10-18-0346 (2).csv`
- Full join audit results: `/Users/sahilgarg/Downloads/Untitled 3_2026-10-18-0410.csv`
- Join audit SQL: `outputs/join-audit/verify_joins.sql`
- Official 2020 B01002E definition: https://data.census.gov/table/ACSDT5Y2020.B01002E
- Census ZCTA guidance: https://www.census.gov/programs-surveys/geography/guidance/geo-areas/zctas.html
