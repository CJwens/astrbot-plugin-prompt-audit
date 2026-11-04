# Prompt Audit Plugin Development Rules

## Accuracy and claims

- Treat provider-reported usage as evidence reported by the provider, not proof of billing or server-side cache behavior.
- Never count streaming chunks as a completed invocation's usage. Count only the final non-chunk response; report a missing final response separately.
- Count each completed `text_chat` or `text_chat_stream` Provider invocation once. Do not claim visibility into retries hidden inside a Provider implementation or calls that bypass registered Provider instances.
- Do not combine cache ratios across different provider/model pairs as if they were directly comparable. Present per-model totals and clearly label any global token-weighted aggregate.
- Distinguish missing usage, zero cached tokens, zero input tokens, and missing final responses.

## Privacy and lifecycle

- Never log or export prompts, conversation text, tool arguments, credentials, raw session IDs, or raw conversation IDs.
- Automatically persist `system_prompt` and `prompt` only for requests where a registered `on_llm_request` handler changes either field. Store initial/final text and per-handler before/after text in the separate local prompt database; cap each text value at 100,000 characters, changed handlers at 32, per-handler mutation text at 200,000 characters, retained requests at 100, and retention at 24 hours.
- Keep prompt text out of ordinary diagnostic events, logs, and CSV exports. Load it only on demand in the matching request detail, use text-only DOM rendering, and provide immediate deletion.
- Do not capture contexts, conversation history, tool arguments, credentials, or provider wire payloads.
- Persist only bounded metadata, hashes, token counters, and provider/model identifiers needed for diagnosis.
- Any UI that reveals captured prompt text must use text-only DOM rendering and clearly state the capture and observation limits.
- Any monkey-patched method or handler must be restored only if it is still the wrapper installed by this plugin. Never overwrite a wrapper installed later by another extension.
- Observer failures must not change or fail the user's LLM request.
- Prefer a plugin-only implementation over an AstrBot core fork; state clearly when plugin instrumentation relies on internal interfaces rather than a stable public API.
- Re-scan registered Provider instances before requests so normal runtime reloads are observed; document any remaining coverage gaps.

## Validation

- Add focused tests for normal calls, streaming final responses, missing final responses, observer errors, and unregister/restore behavior.
- Run Python compilation, JavaScript syntax checks, focused tests, and `git diff --check` when tools are available.
- Update the README whenever measurement scope or limitations change. Prefer explicit uncertainty over a confident but unverified cache-hit claim.
