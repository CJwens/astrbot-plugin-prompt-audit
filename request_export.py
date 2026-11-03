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
    handler_trace = details.get(
        "request_handler_trace", details.get("handler_trace", [])
    )
    handler_inventory = details.get(
        "request_handler_inventory", details.get("handler_inventory", [])
    )
    request_mutations = details.get(
        "request_mutation_chain", mutation_chain or []
    )

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
        "request_mutation_chain": request_mutations,
        "request_handler_trace": handler_trace,
        "request_handler_trace_count": details.get(
            "request_handler_trace_count",
            details.get("handler_trace_count", len(handler_trace)),
        ),
        "request_handler_trace_truncated": details.get(
            "request_handler_trace_truncated",
            details.get("handler_trace_truncated", False),
        ),
        "request_handler_inventory": handler_inventory,
        "request_handler_inventory_count": details.get(
            "request_handler_inventory_count",
            details.get("handler_inventory_count", len(handler_inventory)),
        ),
        "request_handler_inventory_truncated": details.get(
            "request_handler_inventory_truncated",
            details.get("handler_inventory_truncated", False),
        ),
        "request_mutations_truncated": details.get(
            "request_mutations_truncated",
            details.get("mutations_truncated", False),
        ),
        "provider_request_snapshot": details.get(
            "provider_request_snapshot", {}
        ),
        "request_snapshot_fingerprint": snapshot_fingerprint,
    }
