from __future__ import annotations

from collections.abc import Callable
from typing import Any


def request_export_fields(
    details: dict[str, Any],
    fingerprint: Callable[[Any], str],
) -> dict[str, Any]:
    mutation_chain = details.get("mutation_chain")
    after = details.get("after")
    mutation_count = details.get("request_mutation_count")
    snapshot_fingerprint = details.get("request_snapshot_fingerprint", "")

    if mutation_count in (None, "") and isinstance(mutation_chain, list):
        mutation_count = len(mutation_chain)
    if not snapshot_fingerprint and isinstance(after, dict):
        snapshot_fingerprint = fingerprint(after)

    return {
        "request_hook_changed": details.get(
            "request_hook_changed", details.get("hook_changed", [])
        ),
        "request_hook_plugins": details.get(
            "request_hook_plugins", details.get("mutation_plugins", [])
        ),
        "request_hook_handlers": details.get(
            "request_hook_handlers", details.get("mutation_handlers", [])
        ),
        "request_mutation_count": mutation_count if mutation_count is not None else "",
        "request_snapshot_fingerprint": snapshot_fingerprint,
    }
