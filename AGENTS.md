# Prompt Audit Plugin Development Rules

## Accuracy and claims

- Treat provider-reported usage as evidence reported by the provider, not proof of billing or server-side cache behavior.
- Never count streaming chunks as a completed invocation's usage. Count only the final non-chunk response; report a missing final response separately.
- Count each completed `text_chat` or `text_chat_stream` Provider invocation once. Do not claim visibility into retries hidden inside a Provider implementation or calls that bypass registered Provider instances.
- Do not combine cache ratios across different provider/model pairs as if they were directly comparable. Present per-model totals and clearly label any global token-weighted aggregate.
- Distinguish missing usage, zero cached tokens, zero input tokens, and missing final responses.

## Privacy and lifecycle

- By default, never persist or log prompts, conversation text, tool arguments, credentials, raw session IDs, or raw conversation IDs.
- The only prompt-text exception is the dashboard's explicitly armed one-shot capture: it may persist only `system_prompt` and `prompt` for the next request, capped at 100,000 characters per text value and 32 changed handlers, in a separate local database for at most 24 hours. Keep it out of ordinary logs and CSV exports, provide immediate deletion, and do not capture contexts, tool arguments, credentials, or provider wire payloads.
- Persist only bounded metadata, hashes, token counters, and provider/model identifiers needed for diagnosis.
- Any UI that reveals captured prompt text must use text-only DOM rendering and clearly state the privacy and coverage limits before arming capture.
- Any monkey-patched method or handler must be restored only if it is still the wrapper installed by this plugin. Never overwrite a wrapper installed later by another extension.
- Observer failures must not change or fail the user's LLM request.
- Prefer a plugin-only implementation over an AstrBot core fork; state clearly when plugin instrumentation relies on internal interfaces rather than a stable public API.
- Re-scan registered Provider instances before requests so normal runtime reloads are observed; document any remaining coverage gaps.

## Validation

- Add focused tests for normal calls, streaming final responses, missing final responses, observer errors, and unregister/restore behavior.
- Run Python compilation, JavaScript syntax checks, focused tests, and `git diff --check` when tools are available.
- Update the README whenever measurement scope or limitations change. Prefer explicit uncertainty over a confident but unverified cache-hit claim.
