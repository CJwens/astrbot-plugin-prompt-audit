from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any


def _tool_schemas(func_tool: Any) -> list[dict[str, Any]]:
    tools = list(getattr(func_tool, "tools", ()) or ())
    return [
        {
            "name": getattr(tool, "name", ""),
            "description": getattr(tool, "description", ""),
            "parameters": getattr(tool, "parameters", {}),
        }
        for tool in tools
    ]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def capture_request_material(
    req: Any,
    fingerprint: Callable[[Any], str],
) -> dict[str, Any]:
    tool_schemas = _tool_schemas(getattr(req, "func_tool", None))
    return {
        "system": req.system_prompt or "",
        "prompt": req.prompt or "",
        "contexts": [fingerprint(item) for item in req.contexts or []],
        "tools": [fingerprint(item) for item in tool_schemas],
        "extra": [
            fingerprint(item) for item in req.extra_user_content_parts or []
        ],
        "images": [fingerprint(item) for item in req.image_urls or []],
        "audios": [fingerprint(item) for item in req.audio_urls or []],
        "tool_results": [
            fingerprint(item)
            for item in _as_list(getattr(req, "tool_calls_result", None))
        ],
        "model": req.model or "provider-default",
    }


def capture_provider_request_snapshot(
    arguments: dict[str, Any],
    fingerprint: Callable[[Any], str],
    headers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text_fields = ("system_prompt", "prompt")
    snapshot: dict[str, Any] = {}
    for field in text_fields:
        value = arguments.get(field) or ""
        snapshot["system" if field == "system_prompt" else field] = (
            f"{fingerprint(value)}:{len(value)}"
        )

    contexts = arguments.get("contexts") or []
    func_tool = arguments.get("func_tool")
    tool_schemas = _tool_schemas(func_tool)
    list_values = {
        "contexts": contexts,
        "tools": tool_schemas,
        "images": arguments.get("image_urls") or [],
        "audios": arguments.get("audio_urls") or [],
        "extra": arguments.get("extra_user_content_parts") or [],
        "tool_results": _as_list(arguments.get("tool_calls_result")),
    }
    for field, value in list_values.items():
        snapshot[field] = f"{fingerprint(value)}:{len(value)}"

    model = arguments.get("model") or "provider-default"
    snapshot["model"] = f"{fingerprint(model)}:{len(str(model))}"
    tool_choice = arguments.get("tool_choice", "auto")
    snapshot["tool_choice"] = fingerprint(tool_choice)
    request_options = {
        key: arguments[key]
        for key in (
            "response_format",
            "parallel_tool_calls",
            "reasoning_effort",
            "verbosity",
            "temperature",
            "top_p",
            "max_tokens",
            "max_completion_tokens",
            "stop",
            "seed",
        )
        if key in arguments
    }
    snapshot["request_options"] = fingerprint(request_options)
    sensitive_header_markers = (
        "authorization",
        "api-key",
        "apikey",
        "token",
        "secret",
        "password",
        "cookie",
        "credential",
        "signature",
    )
    snapshot["headers"] = {
        str(name): (
            {"value": "redacted", "present": bool(value)}
            if any(
                marker in str(name).casefold()
                for marker in sensitive_header_markers
            )
            else {
                "fingerprint": fingerprint(str(value)),
                "chars": len(str(value)),
            }
        )
        for name, value in (headers or {}).items()
    }
    return snapshot


def describe_handler_inventory(
    handlers: list[Any],
    eligible_handlers: list[Any],
    plugin_lookup: Callable[[str], Any],
) -> list[dict[str, Any]]:
    eligible_ids = {id(handler) for handler in eligible_handlers}
    inventory = []
    for handler in handlers:
        module_path = str(getattr(handler, "handler_module_path", "") or "")
        plugin = plugin_lookup(module_path)
        plugin_name = str(
            getattr(plugin, "name", "") or module_path or "unknown"
        )
        if not getattr(handler, "enabled", True):
            status = "handler_disabled"
        elif plugin is None:
            status = "plugin_unavailable"
        elif not getattr(plugin, "activated", True):
            status = "plugin_inactive"
        elif id(handler) not in eligible_ids:
            status = "plugin_filtered"
        else:
            status = "eligible"
        inventory.append(
            {
                "plugin": plugin_name,
                "plugin_module": module_path,
                "handler": str(getattr(handler, "handler_name", "") or ""),
                "handler_full_name": str(
                    getattr(handler, "handler_full_name", "") or ""
                ),
                "priority": getattr(handler, "extras_configs", {}).get(
                    "priority", 0
                ),
                "plugin_active": bool(
                    plugin is not None
                    and getattr(plugin, "activated", True)
                ),
                "handler_enabled": bool(getattr(handler, "enabled", True)),
                "eligible": status == "eligible",
                "status": status,
            }
        )
    return inventory


def _text_change(before: str, after: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": "text_rewritten",
        "before_chars": len(before),
        "after_chars": len(after),
        "added_prefix_chars": 0,
        "added_suffix_chars": 0,
        "removed_prefix_chars": 0,
        "removed_suffix_chars": 0,
    }
    if not before:
        result["kind"] = "text_initialized"
    elif after.startswith(before) and after.endswith(before):
        result["kind"] = "text_extension_position_ambiguous"
    elif after.startswith(before):
        result["kind"] = "text_appended"
        result["added_suffix_chars"] = len(after) - len(before)
    elif after.endswith(before):
        result["kind"] = "text_prepended"
        result["added_prefix_chars"] = len(after) - len(before)
    elif before in after:
        offset = after.find(before)
        result["kind"] = "text_wrapped"
        result["added_prefix_chars"] = offset
        result["added_suffix_chars"] = len(after) - offset - len(before)
    elif after in before:
        offset = before.find(after)
        result["kind"] = "text_trimmed"
        result["removed_prefix_chars"] = offset
        result["removed_suffix_chars"] = len(before) - offset - len(after)
    elif before.startswith(after):
        result["kind"] = "text_suffix_removed"
        result["removed_suffix_chars"] = len(before) - len(after)
    elif before.endswith(after):
        result["kind"] = "text_prefix_removed"
        result["removed_prefix_chars"] = len(before) - len(after)
    return result


def _is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    needle_index = 0
    for item in haystack:
        if needle_index < len(needle) and needle[needle_index] == item:
            needle_index += 1
    return needle_index == len(needle)


def _list_change(before: list[str], after: list[str]) -> dict[str, Any]:
    before_counts = Counter(before)
    after_counts = Counter(after)
    added_count = sum((after_counts - before_counts).values())
    removed_count = sum((before_counts - after_counts).values())
    result: dict[str, Any] = {
        "kind": "items_rewritten",
        "before_items": len(before),
        "after_items": len(after),
        "added_items": added_count,
        "removed_items": removed_count,
    }
    if not before and after:
        result["kind"] = "items_initialized"
    elif before and not after:
        result["kind"] = "items_cleared"
    elif not removed_count and added_count:
        if after[: len(before)] == before:
            result["kind"] = "items_appended"
        elif before and after[-len(before) :] == before:
            result["kind"] = "items_prepended"
        elif _is_subsequence(before, after):
            result["kind"] = "items_inserted_around_existing"
    elif not added_count and removed_count:
        if _is_subsequence(after, before):
            result["kind"] = "items_removed"
    elif not added_count and not removed_count:
        result["kind"] = "items_reordered"
    return result


def describe_request_mutation(
    before: dict[str, Any],
    after: dict[str, Any],
    fingerprint: Callable[[Any], str],
) -> dict[str, dict[str, Any]]:
    delta: dict[str, dict[str, Any]] = {}
    for field, before_value in before.items():
        after_value = after.get(field)
        if before_value == after_value:
            continue
        if field in {"system", "prompt"}:
            detail = _text_change(str(before_value or ""), str(after_value or ""))
        elif field in {
            "contexts",
            "tools",
            "extra",
            "images",
            "audios",
            "tool_results",
        }:
            detail = _list_change(
                list(before_value or []),
                list(after_value or []),
            )
        else:
            detail = {"kind": "value_changed"}
        detail["before_hash"] = fingerprint(before_value)
        detail["after_hash"] = fingerprint(after_value)
        delta[field] = detail
    return delta
