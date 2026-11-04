from __future__ import annotations

from collections import Counter
from typing import Any


def _snapshot_change_summary(
    before: dict[str, Any], after: dict[str, Any]
) -> str:
    labels = {
        "system": "system",
        "prompt": "prompt",
        "contexts": "上下文",
        "tools": "工具",
        "extra": "附加内容",
        "images": "图片",
        "audios": "音频",
        "tool_results": "工具结果",
        "model": "模型",
    }
    changes = [
        f"{labels.get(key, key)}: {before.get(key, '—')} → {value}"
        for key, value in after.items()
        if before.get(key) != value
    ]
    return "; ".join(changes) or "未见请求字段变化"


def _mutation_summary(trace: list[dict[str, Any]]) -> str:
    field_labels = {
        "contexts": "上下文",
        "tools": "工具",
        "extra": "附加内容",
        "images": "图片",
        "audios": "音频",
        "tool_results": "工具结果",
        "system": "system",
        "prompt": "prompt",
    }
    summaries = []
    for item in trace:
        delta = item.get("delta") or {}
        if not delta:
            continue
        changes = []
        for field, detail in delta.items():
            kind = detail.get("kind", "value_changed")
            if field in {"contexts", "tools", "extra", "images", "audios", "tool_results"}:
                change = (
                    f"{kind}: +{detail.get('added_items', 0)}"
                    f"/-{detail.get('removed_items', 0)} 项"
                )
            elif field in {"system", "prompt"}:
                change = (
                    f"{kind}: {detail.get('before_chars', 0)}→"
                    f"{detail.get('after_chars', 0)} 字符"
                )
            else:
                change = kind
            changes.append(f"{field_labels.get(field, field)} {change}")
        summaries.append(
            f"{item.get('plugin', '未知插件')}/{item.get('handler', '未知处理器')}: "
            + ", ".join(changes)
        )
    return " | ".join(summaries)


def _usage_status_label(status: str) -> str:
    return {
        "cached_tokens_reported": "服务商报告有缓存",
        "zero_cached_tokens_reported": "服务商报告缓存为 0",
        "no_input_tokens_reported": "未报告有效输入 token",
        "usage_unavailable": "服务商未返回 usage",
        "final_response_missing": "未观察到最终流响应",
        "provider_call_failed": "Provider 调用失败",
        "usage_parse_failed": "usage 解析失败",
    }.get(status, status or "未知")


def build_human_csv_export(
    events: list[dict[str, Any]],
    *,
    hours: int,
    generated_at: str,
    limited_to: int,
) -> dict[str, Any]:
    analysis = build_analysis_export(
        events,
        hours=hours,
        generated_at=generated_at,
        limited_to=limited_to,
    )
    columns = [
        "记录类型",
        "时间（UTC）",
        "请求ID",
        "会话范围",
        "服务商",
        "模型",
        "状态",
        "缓存输入token",
        "未缓存输入token",
        "总输入token",
        "报告缓存率",
        "输出token",
        "请求字段变化",
        "逐处理器变更摘要",
        "同会话上一请求变化",
        "请求快照变化（指纹:数量/长度）",
        "关联状态",
        "说明",
    ]
    rows = []

    def make_row(
        *,
        record_type: str,
        created_at: str,
        request_id: str = "",
        scope: str = "",
        provider_id: str = "",
        model: str = "",
        status: str = "",
        input_cached: Any = "",
        input_other: Any = "",
        input_total: Any = "",
        cache_ratio: Any = None,
        output: Any = "",
        hook_changed: list[str] | str | None = None,
        mutation_summary: str = "",
        previous_turn_changed: list[str] | None = None,
        snapshot_change: str = "",
        association_status: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        fields = (
            hook_changed
            if isinstance(hook_changed, list)
            else [hook_changed] if hook_changed else []
        )
        ratio = (
            f"{float(cache_ratio) * 100:.2f}%"
            if cache_ratio is not None
            else ""
        )
        return {
            "记录类型": record_type,
            "时间（UTC）": created_at,
            "请求ID": request_id,
            "会话范围": scope,
            "服务商": provider_id,
            "模型": model,
            "状态": _usage_status_label(status) if status else "",
            "缓存输入token": input_cached,
            "未缓存输入token": input_other,
            "总输入token": input_total,
            "报告缓存率": ratio,
            "输出token": output,
            "请求字段变化": ", ".join(fields),
            "逐处理器变更摘要": mutation_summary,
            "同会话上一请求变化": ", ".join(previous_turn_changed or []),
            "请求快照变化（指纹:数量/长度）": snapshot_change,
            "关联状态": association_status,
            "说明": note,
        }

    for item in analysis["requests"]:
        trace = item.get("handler_trace") or []
        mutation_summary = _mutation_summary(trace)
        scope_type = item.get("scope_type", "")
        scope = (
            item.get("group_name")
            or item.get("user_name")
            or scope_type
            or ""
        )
        if not item["provider_calls"]:
            rows.append(
                make_row(
                    record_type="请求未见Provider用量",
                    created_at=item["created_at"],
                    request_id=item["request_id"],
                    scope=scope,
                    model=item.get("model", ""),
                    hook_changed=item.get("hook_changed"),
                    mutation_summary=mutation_summary,
                    previous_turn_changed=item.get("previous_turn_changed"),
                    snapshot_change=_snapshot_change_summary(
                        item.get("before") or {},
                        item.get("after") or {},
                    ),
                    note="请求钩子记录存在，但导出范围内没有关联到 Provider usage。",
                )
            )
        for call in item["provider_calls"]:
            rows.append(
                make_row(
                    record_type="Provider调用",
                    created_at=call["created_at"],
                    request_id=item["request_id"],
                    scope=scope,
                    provider_id=call.get("provider_id", ""),
                    model=call.get("model") or item.get("model", ""),
                    status=call.get("status", ""),
                    input_cached=call.get("input_cached", ""),
                    input_other=call.get("input_other", ""),
                    input_total=call.get("input_total", ""),
                    cache_ratio=call.get("cache_ratio"),
                    output=call.get("output", ""),
                    hook_changed=item.get("hook_changed"),
                    mutation_summary=mutation_summary,
                    previous_turn_changed=item.get("previous_turn_changed"),
                    snapshot_change=_snapshot_change_summary(
                        item.get("before") or {},
                        item.get("after") or {},
                    ),
                    association_status=call.get("association_status", ""),
                    note=(
                        "缓存率是服务商报告值；快照指纹仅示差异，不证明缓存键变化或未命中。"
                    ),
                )
            )

    for call in analysis["usage_without_associated_request"]:
        rows.append(
            make_row(
                record_type="Provider用量未关联请求",
                created_at=call["created_at"],
                request_id=call.get("invocation_id", ""),
                provider_id=call.get("provider_id", ""),
                model=call.get("model", ""),
                status=call.get("status", ""),
                input_cached=call.get("input_cached", ""),
                input_other=call.get("input_other", ""),
                input_total=call.get("input_total", ""),
                cache_ratio=call.get("cache_ratio"),
                output=call.get("output", ""),
                hook_changed=call.get("request_hook_changed"),
                snapshot_change="请求未关联，无法进行快照对比",
                association_status=call.get("association_status", ""),
                note=(
                    "用量未关联请求：没有足够证据绑定到具体请求；"
                    "不要按时间邻近归因。"
                ),
            )
        )

    rows.sort(key=lambda row: (row["时间（UTC）"], row["请求ID"]))
    return {
        "hours": hours,
        "generated_at": generated_at,
        "columns": columns,
        "rows": rows,
        "limited_to": limited_to,
    }


def _compact_inventory(inventory: list[dict[str, Any]]) -> dict[str, Any]:
    plugins: dict[str, dict[str, Any]] = {}
    for item in inventory:
        plugin = str(item.get("plugin", "unknown"))
        entry = plugins.setdefault(
            plugin,
            {"statuses": Counter(), "handlers": []},
        )
        entry["statuses"][str(item.get("status", "unknown"))] += 1
        handler = str(item.get("handler", ""))
        if handler and handler not in entry["handlers"]:
            entry["handlers"].append(handler)
    return {
        plugin: {
            "statuses": dict(sorted(item["statuses"].items())),
            "handlers": item["handlers"],
        }
        for plugin, item in sorted(plugins.items())
    }


def build_analysis_export(
    events: list[dict[str, Any]],
    *,
    hours: int,
    generated_at: str,
    limited_to: int,
) -> dict[str, Any]:
    requests: dict[tuple[str, str], dict[str, Any]] = {}
    usage_without_request: list[dict[str, Any]] = []
    usage_events: list[dict[str, Any]] = []

    for event in events:
        details = event.get("details") or {}
        kind = event.get("kind")
        if kind == "request":
            key = (
                str(event.get("request_id", "")),
                str(event.get("session_hash", "")),
            )
            before = details.get("before") or {}
            after = details.get("after") or {}
            requests.setdefault(
                key,
                {
                    "created_at": event.get("created_at", ""),
                    "request_id": key[0],
                    "session_hash": key[1],
                    "scope_type": details.get("scope_type", ""),
                    "group_name": details.get("group_name", ""),
                    "user_name": details.get("user_name", ""),
                    "model": after.get("model", before.get("model", "")),
                    "hook_changed": details.get("hook_changed", []),
                    "previous_turn_changed": details.get(
                        "previous_turn_changed", []
                    ),
                    "previous_seen": details.get("previous_seen"),
                    "before": before,
                    "after": after,
                    "mutation_count": details.get(
                        "request_mutation_count", 0
                    ),
                    "mutations_truncated": details.get(
                        "request_mutations_truncated",
                        details.get("mutations_truncated", False),
                    ),
                    "handler_trace": details.get(
                        "handler_trace",
                        details.get("request_handler_trace", []),
                    ),
                    "handler_trace_count": details.get(
                        "handler_trace_count",
                        details.get("request_handler_trace_count", 0),
                    ),
                    "handler_trace_truncated": details.get(
                        "handler_trace_truncated",
                        details.get(
                            "request_handler_trace_truncated", False
                        ),
                    ),
                    "handler_inventory": _compact_inventory(
                        details.get(
                            "handler_inventory",
                            details.get("request_handler_inventory", []),
                        )
                    ),
                    "handler_inventory_count": details.get(
                        "handler_inventory_count",
                        details.get("request_handler_inventory_count", 0),
                    ),
                    "handler_inventory_truncated": details.get(
                        "handler_inventory_truncated",
                        details.get(
                            "request_handler_inventory_truncated", False
                        ),
                    ),
                    "provider_calls": [],
                },
            )
        elif kind == "usage":
            usage_events.append(event)

    for event in usage_events:
        details = event.get("details") or {}
        request_id = str(details.get("llm_request_id", ""))
        session_hash = str(event.get("session_hash", ""))
        call = {
            "created_at": event.get("created_at", ""),
            "invocation_id": event.get("request_id", ""),
            "provider_id": details.get("provider_id", ""),
            "model": details.get("model", ""),
            "status": details.get("status", ""),
            "input_cached": details.get("input_cached", 0),
            "input_other": details.get("input_other", 0),
            "input_total": details.get("input_total", 0),
            "output": details.get("output", 0),
            "cache_ratio": details.get("cache_ratio"),
            "association_status": details.get(
                "association_status", "unassociated"
            ),
            "association_source": details.get("association_source", ""),
            "association_age_ms": details.get("association_age_ms"),
            "request_hook_changed": details.get(
                "request_hook_changed", []
            ),
            "provider_request_snapshot": details.get(
                "provider_request_snapshot", {}
            ),
            "request_snapshot_fingerprint": details.get(
                "request_snapshot_fingerprint", ""
            ),
        }
        request = requests.get((request_id, session_hash))
        if request is None or details.get("association_status") != "associated":
            call["association_reason"] = (
                "request_event_not_in_export_window_or_retention"
                if details.get("association_status") == "associated"
                else "request_not_confidently_associated"
            )
            usage_without_request.append(call)
        else:
            request["provider_calls"].append(call)

    ordered_requests = sorted(
        requests.values(),
        key=lambda item: (item["created_at"], item["request_id"]),
    )
    for item in ordered_requests:
        item["provider_calls"].sort(
            key=lambda call: (call["created_at"], call["invocation_id"])
        )

    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "window_hours": hours,
        "measurement_scope": (
            "AstrBot on_llm_request handler observations and registered Provider "
            "text_chat/text_chat_stream calls; provider usage is reported data."
        ),
        "limits": {
            "events_read": len(events),
            "maximum_events": limited_to,
            "requests_without_provider_usage": sum(
                not item["provider_calls"] for item in ordered_requests
            ),
            "usage_without_associated_request": len(usage_without_request),
        },
        "privacy": {
            "includes_prompt_or_context_text": False,
            "includes_tool_arguments": False,
            "fingerprints_are_not_cache_key_proof": True,
        },
        "requests": ordered_requests,
        "usage_without_associated_request": usage_without_request,
    }
