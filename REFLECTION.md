# Reflection

## Architectural Blueprint & Technical Decisions

I used a spec-driven process: clarify intended behavior, write a design and implementation plan, then divide work into tasks that could be developed in separate worktrees when their interfaces were clear. This made parallel work easier to review and integrate, but still required coordination around contracts and integration issues.

The architecture separates the user interface, request routing, agent reasoning, and data access. The scope gate runs before the ReAct agent so clearly unrelated requests do not reach the data tools. In this MVP, that gate is rule-based. Dataset questions continue to the ADK agent; unclear requests receive a clarification, and general Census explanations receive a limitation response because approved-document retrieval is not connected yet.

![Census Compass architecture diagram](docs/architecture.svg)

The editable diagrams.net source is [docs/architecture.drawio](docs/architecture.drawio), with the rendered vector graphic at [docs/architecture.svg](docs/architecture.svg). It distinguishes the live dataset path from the general Census-document path, which is not connected in this MVP. Conversation and runtime state are held in memory, so a process restart loses them.

I added a semantic layer over the raw dataset to describe tables, fields, measures, dimensions, grain, and relationships. Schema search uses those descriptions to narrow candidate tables; the agent can then request a table's full definition before generating SQL. This reduces how much schema the model must consider and gives it guidance about interpretation and aggregation. It lowers the risk of hallucinated field meanings, but cannot eliminate it when source metadata is incomplete or ambiguous.

Resolvers for year, geography, and population group turn user language into explicit filters and allow clarification when an entity is ambiguous. Periods require care: ACS five-year estimates are overlapping multi-year periods, not single-year observations. The dataset also does not establish a verified mapping for ZIP-code answers.

SQLGlot-based checks validate and constrain generated SQL deterministically rather than relying only on the model to catch its own mistakes. These checks help enforce query boundaries, but do not prove every query is statistically meaningful. Rules about measure grain and aggregation remain necessary.

## Time Investment & Prioritization Under Constraints

Under the time constraint, I prioritized an end-to-end path: scope-check the request, resolve key entities, retrieve relevant schema, generate and validate a query, execute it, and present the result. I focused on semantic definitions and deterministic SQL checks because a fluent answer based on a misinterpreted field or invalid aggregation would be especially misleading.

I deferred work that was not necessary to demonstrate that path. General Census documentation retrieval is not connected in the current MVP, and production hosting credentials and deployment hardening remain separate work. A public-facing service would also need operational controls such as rate limiting, monitoring, and clear policies for service failures.

With more time, I would first strengthen and evaluate guardrail behavior, especially its failure policy, and broaden tests around statistical correctness and unsupported requests. I would then build a maintained documentation retrieval pipeline with reviewed, versioned sources and answer-level source attribution, followed by deployment hardening and monitoring.

## Graceful Degradation & Edge Case Handling

Several failure modes have been identified but are not fully addressed:

- **Scope-gate coverage:** The current pre-agent gate uses rules. Novel wording, negation, or mixed Census and unrelated requests may be misclassified. Its behavior should be measured on a representative evaluation set and made conservative when uncertain.
- **Ambiguous entities:** Place names may refer to multiple counties or states. Resolvers can surface ambiguity, but aliases, misspellings, and incomplete phrasing need broader coverage.
- **Unsupported geography:** ZIP-code questions lack a verified geographic mapping. The agent should state this limitation instead of substituting a nearby geography.
- **Time-period interpretation:** ACS five-year estimates describe multi-year periods, and 2019 and 2020 periods overlap. Treating them as annual observations or directly comparable yearly values could mislead users.
- **Measures and aggregation:** Averaging block-group medians does not generally produce the county median. Table relationships also require verified join keys and cardinality; shared column names alone do not establish a valid join.
- **Incomplete metadata:** Fields without reliable descriptions should remain unresolved until verified against authoritative documentation, rather than inferred from codes or neighboring fields.
- **Execution and provider failures:** Snowflake or the model provider may be unavailable or slow. Timeouts, result limits, and safe error messages help, but operational monitoring and systematic failure-path testing need further work.
- **In-memory state:** Conversation history and runtime state are held in memory for the MVP. A process restart loses them, and a multi-instance deployment would need an explicit shared session strategy if cross-request persistence is required.

The key principle is to avoid returning a plausible number when the geography, period, measure, aggregation, or source does not support it.

## Testing Strategy & Quality Assurance

The project includes automated tests for core behavior such as entity resolution, schema and glossary tools, SQL validation, and guardrail decisions. I also manually exercised the interface and agent flow, including an ambiguous Orange County question that should prompt for clarification.

Live Snowflake and model-provider integration tests are opt-in. This keeps routine tests independent of credentials and remote service availability, but a passing local suite does not by itself establish that a deployed system can connect to those services or meet its response-time target.

With more time, I would add an evaluation suite of realistic questions with expected outcomes, including cases where the correct result is a clarification or refusal. It should check not only final wording but also selected tables, generated SQL, geography and year filters, aggregation logic, and unsupported-question handling. I would run it in CI, add integration checks against a controlled Snowflake environment, and track latency and failure rates against the 60-second target.

## AI Tooling Usage Disclosure

I used AI tools to support brainstorming, specification drafting, implementation, and review. I treated generated code and design suggestions as proposals, made the architectural choices, reviewed the changes, and used automated tests and manual checks to verify behavior. AI assistance does not replace independent review of query correctness, statistical interpretation, security, or production readiness.
