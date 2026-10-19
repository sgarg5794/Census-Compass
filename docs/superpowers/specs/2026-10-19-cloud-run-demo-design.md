# Cloud Run public demo design

## Goal

Deploy Census Compass as a public, no-login demo on Google Cloud Run and keep one warm instance available for five days from deployment.

## Context and constraints

- The current UI is a Streamlit app in `ui/app.py`.
- Conversation and ADK runtime state currently live in process/browser memory. This is acceptable for the MVP: context must work across turns in a browser session, while a container restart or deployment may clear an active conversation.
- Snowflake connections currently depend on a password stored in the developer's macOS Keychain. That mechanism cannot run on Cloud Run.
- The local LanceDB search index is approximately 15 MB. The embedding model is loaded through `sentence-transformers` and should be available without requiring a first-user download.
- The demo is intentionally public and does not require visitors to sign in. The Snowflake and model-provider credentials must remain server-side.
- The five-day warm period starts when the service is deployed, not on the date this document is written.

## Chosen design

### Hosting and scaling

- Package the Streamlit app and its runtime dependencies as a container and deploy it to Cloud Run in a US region.
- Allow unauthenticated access to the app's HTTPS endpoint, as required for the public demo.
- Configure request-based billing, `min-instances=1`, and `max-instances=1` for the five-day demo window. A single instance keeps the current in-memory ADK runtime and visitor/conversation mapping on one process while limiting compute exposure. The small expected evaluator audience is the operating assumption. In-memory state is an accepted MVP tradeoff; active conversations can be lost if the instance restarts.
- Keep the service-level minimum at one for 120 hours after deployment, then set it to zero. The deployment runbook must include the exact expiry time and the command to reset the minimum. The service remains deployed and public after that; it may cold-start after idling.
- Add a Cloud Billing budget alert and document that alerts notify but do not cap spending. Do not add a load balancer, VPC connector, custom domain, or managed database for this demo.

Cloud Run minimum instances are best-effort availability, not an uptime guarantee. A container crash, rollout, quota/billing issue, or platform event can still cause an interruption or restart.

### Snowflake authentication

- Use Snowflake Workload Identity Federation (WIF) with a dedicated Snowflake `SERVICE` user bound to the Cloud Run service account.
- Grant that user only the existing `CENSUS_READER` role and the minimum required warehouse/database/schema access. Do not use the developer's personal Snowflake login or password on the server.
- Keep local development's Keychain authentication intact; choose the auth method from configuration so the same connection module can use Keychain locally and WIF on Cloud Run.
- Snowflake administrator action is a prerequisite: create/configure the service user and grant its role. The app deployment must not proceed with a personal credential as a fallback.

### Application secrets and packaged assets

- Store the LiteLLM/Anthropic API key in Google Secret Manager and grant the Cloud Run service identity access only to that secret.
- Supply non-secret Snowflake account, role, warehouse, database, and schema settings through Cloud Run environment configuration.
- Include the built LanceDB index and pinned embedding model in the deployable image (or an equivalent immutable, versioned artifact) so a user's first question does not trigger a model download.
- Do not persist prompts, results, or conversation contents in Cloud Logging. Logs should contain operational events and sanitized error details only.

## Not in scope

- Durable conversation history across browser sessions, container restarts, or deployments.
- User accounts, authentication, or per-user authorization.
- Production high availability, multi-region failover, autoscaling beyond one instance, custom domain, or formal uptime SLA.
- Replacing the LLM provider or changing the existing query and guardrail behavior except where required to deploy it safely.

## Acceptance criteria

1. The app is reachable on a public HTTPS URL without visitor sign-in.
2. A normal multi-turn chat works in one browser session and reaches the existing ADK agent.
3. Snowflake queries authenticate through the dedicated WIF-backed service user and read-only `CENSUS_READER` role; no personal Snowflake password is present in the image, repository, or Cloud Run environment.
4. The LLM API key is read from Secret Manager and is never returned to the browser or logged.
5. Cloud Run is configured with one minimum and one maximum instance during the demo window.
6. The deployment runbook records the 120-hour expiry and the command to restore minimum instances to zero.
7. Health checks and a smoke test verify the public UI, a basic agent response, and a read-only Snowflake query before sharing the URL.
8. The operator can see a billing budget alert and understands it is not a hard spending cap.

## Known limitations and operational notes

- The Streamlit UI's local state and ADK runtime state are not durable. If the container restarts, the current chat may need to be restarted. If this proves unacceptable for the demo, durable state is a separate design change.
- With a single maximum instance, concurrent team traffic shares that instance. This is appropriate for a small evaluation but is not a scale target.
- Public access means anyone with the URL can use the agent. Configure a low maximum instance count and review usage/billing during the demo.
- Google Cloud billing must be enabled. Cloud Run may incur charges even during the free-tier period, depending on idle compute, active requests, artifact storage/builds, and remaining account-level free usage.
- The current machine has no `gcloud` executable detected. Google Cloud CLI installation/authentication and project selection are prerequisites to implementation.
- Existing uncommitted changes on `feature/guardrails` are outside this design and must remain untouched.
