# Census Compass — approved Streamlit UI design

Date: 2026-10-18
Status: Visual design approved in conversation; session persistence and deployment remain separate decisions.

## Goal and audience

Provide a clean, public, no-login chat interface for asking data questions about US demographics. The user approved the interactive preview after two corrections: the sidebar contains only New chat and conversation history; welcome prompts ask data questions rather than explain Census fields. This document records that approval and the implementation boundaries.

## Approved experience

- Header: Census Compass, with a short Census-focused subtitle.
- Sidebar: New chat and conversation history only. Do not add dataset navigation, topic lists, schema browsing, coverage navigation, account controls or settings.
- Welcome: a short introduction and four clickable data questions:
  1. What was California’s population in 2020?
  2. Compare the 2020 populations of California and Texas.
  3. How many households rent versus own in San Francisco County?
  4. What was California’s unemployment rate in the 2020-labeled data?
- Selecting a welcome question submits that exact text through the normal message path once.
- Main area: user/assistant conversation; composer at the bottom; Enter sends and Shift+Enter inserts a newline, using native Streamlit chat behavior.
- New chat opens a blank conversation without erasing previous conversations. History selection restores that conversation and its agent session, not merely its visible messages. Titles come deterministically from the first question; no title-generation model call.
- Processing: show concise, factual status labels driven by actual events. Never display model reasoning, prompts, tool arguments or a fictional sequence of tool calls.
- Answers: Markdown text, structured result tables when supplied by the service, and optional expandable View query & source details. Source periods and limitations belong with the answer when material. Do not expose a catalog or retrieved schema. Only executed, backend-approved SQL may be shown; omit the expander if there is no evidence.
- Follow-ups use the selected conversation's context. Clarifications, unsupported questions, off-topic redirects and failures render as assistant messages.
- Small screens: retain usable chat input and history through Streamlit's native sidebar behavior; avoid custom fixed-position overlays and JavaScript controls.

## Connection boundary

Streamlit calls an internal Python ChatService, which calls the agent runtime. No separate HTTP API is required for this UI scope. A server-created anonymous visitor ID and an independent conversation ID identify sessions. Browser input never supplies a trusted visitor identity or unrestricted session lookup.

The future scope check belongs before the ReAct runtime. The UI renders its outcome; it does not implement its own keyword classifier. A missing scope gate must block public live operation, not silently bypass the gate. The live service must use the backend's shared request deadline and existing query validation; the UI must not start a second SQL execution path.

Show only user-facing events: status, final result and safe error. Raw ADK events and internal chain-of-thought are not UI output. Stream final text only if the runtime explicitly provides safe final-answer deltas; otherwise show status then the completed answer.

## Session boundary for this UI increment

The baseline UI keeps its conversation list and transcript in browser-session-scoped Streamlit state. The service holds matching, visitor-isolated ADK sessions for that lifetime. Refresh, disconnect or server restart may lose this history; do not claim durable or cross-device history. This is an interim implementation boundary, not approval of a production memory backend. Production retention, anonymous identity recovery, expiry and durable storage require the later session-memory decision.

Two anonymous visitors must never share conversations, transcript caches or mutable agent state. New chat creates a new agent session. UI reruns must not resend messages. One in-flight turn is allowed per browser session; disable submission and history switching while it is running.

## Explicit exclusions

Do not implement hosting, Snowflake service-user provisioning, the scope classifier, permanent memory, schema search/RAG, model selection, authentication, export, file upload, generated charts, automatic retries or transcript search in this increment. Live runtime integration is conditional on the independently implemented agent and scope gate being available. Test fixtures are development-only; production never silently falls back to scripted answers.

## Acceptance

The four examples submit data questions. The sidebar has only New chat and conversation history. Two conversations retain independent visible histories and agent sessions while the browser session remains active. Rerendering never re-executes a completed request. Safe errors and timeouts leave chat usable. Browser sessions are isolated. A disconnected backend produces an honest unavailable message. No mock Census numbers appear in production. Review the actual Streamlit page at desktop and narrow widths against the approved preview before accepting implementation.

Reference preview: census-compass-ui-preview.html, stored beside this document. Its numbers are illustrative fixtures, not dataset facts.
