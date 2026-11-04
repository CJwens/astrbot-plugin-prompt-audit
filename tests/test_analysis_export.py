from astrbot_plugin_prompt_audit.analysis_export import (
    build_analysis_export,
    build_human_csv_export,
)


def test_analysis_export_joins_provider_calls_and_keeps_context_private():
    events = [
        {
            "created_at": "2026-11-04T10:00:00+00:00",
            "kind": "usage",
            "request_id": "invocation-1",
            "session_hash": "session-hash",
            "details": {
                "llm_request_id": "trace-1",
                "association_status": "associated",
                "provider_id": "provider",
                "model": "model",
                "status": "cached_tokens_reported",
                "input_cached": 40,
                "input_other": 60,
                "input_total": 100,
                "provider_request_snapshot": {"contexts": "ctx-hash:3"},
            },
        },
        {
            "created_at": "2026-11-04T09:59:59+00:00",
            "kind": "request",
            "request_id": "trace-1",
            "session_hash": "session-hash",
            "details": {
                "before": {"contexts": "before-hash:2"},
                "after": {"contexts": "after-hash:3"},
                "handler_trace": [
                    {
                        "plugin": "context-plugin",
                        "handler": "inject_context",
                        "changed": ["contexts"],
                        "delta": {
                            "contexts": {
                                "kind": "items_appended",
                                "added_items": 1,
                                "before_hash": "before-hash",
                                "after_hash": "after-hash",
                            }
                        },
                    }
                ],
                "handler_inventory": [
                    {
                        "plugin": "context-plugin",
                        "handler": "inject_context",
                        "status": "eligible",
                    }
                ],
            },
        },
    ]

    result = build_analysis_export(
        events,
        hours=24,
        generated_at="2026-11-04T10:01:00+00:00",
        limited_to=10000,
    )

    assert len(result["requests"]) == 1
    request = result["requests"][0]
    assert request["request_id"] == "trace-1"
    assert request["handler_trace"][0]["delta"]["contexts"]["kind"] == (
        "items_appended"
    )
    assert request["provider_calls"][0]["input_cached"] == 40
    assert result["usage_without_associated_request"] == []
    assert result["privacy"]["includes_prompt_or_context_text"] is False


def test_analysis_export_keeps_unassociated_usage_separate():
    result = build_analysis_export(
        [
            {
                "created_at": "2026-11-04T10:00:00+00:00",
                "kind": "usage",
                "request_id": "invocation-2",
                "session_hash": "session-hash",
                "details": {
                    "llm_request_id": "missing-trace",
                    "association_status": "unassociated",
                    "input_total": 10,
                },
            }
        ],
        hours=24,
        generated_at="2026-11-04T10:01:00+00:00",
        limited_to=10000,
    )

    assert len(result["usage_without_associated_request"]) == 1
    assert result["limits"]["usage_without_associated_request"] == 1


def test_human_csv_export_flattens_calls_and_labels_missing_associations():
    events = [
        {
            "created_at": "2026-11-04T10:00:00+00:00",
            "kind": "request",
            "request_id": "trace-1",
            "session_hash": "session-hash",
            "details": {
                "scope_type": "group",
                "group_name": "测试群",
                "before": {"contexts": "before:2"},
                "after": {"contexts": "after:3"},
                "handler_trace": [
                    {
                        "plugin": "context-plugin",
                        "handler": "inject_context",
                        "delta": {
                            "contexts": {
                                "kind": "items_appended",
                                "added_items": 1,
                                "removed_items": 0,
                            }
                        },
                    }
                ],
            },
        },
        {
            "created_at": "2026-11-04T10:00:01+00:00",
            "kind": "usage",
            "request_id": "call-1",
            "session_hash": "session-hash",
            "details": {
                "llm_request_id": "trace-1",
                "association_status": "associated",
                "provider_id": "provider",
                "model": "model",
                "status": "cached_tokens_reported",
                "input_cached": 40,
                "input_other": 60,
                "input_total": 100,
                "cache_ratio": 0.4,
            },
        },
        {
            "created_at": "2026-11-04T10:00:02+00:00",
            "kind": "usage",
            "request_id": "call-2",
            "session_hash": "other-session",
            "details": {
                "association_status": "unassociated",
                "provider_id": "provider",
                "model": "model",
                "status": "zero_cached_tokens_reported",
                "input_cached": 0,
                "input_other": 10,
                "input_total": 10,
                "cache_ratio": 0,
            },
        },
    ]

    result = build_human_csv_export(
        events,
        hours=24,
        generated_at="2026-11-04T10:01:00+00:00",
        limited_to=10000,
    )

    assert len(result["rows"]) == 2
    associated, unassociated = result["rows"]
    assert associated["记录类型"] == "Provider调用"
    assert associated["会话范围"] == "测试群"
    assert associated["报告缓存率"] == "40.00%"
    assert "context-plugin/inject_context" in associated["逐处理器变更摘要"]
    assert "上下文 items_appended: +1/-0 项" in associated["逐处理器变更摘要"]
    assert "上下文: before:2 → after:3" in associated["请求快照变化（指纹:数量/长度）"]
    assert unassociated["记录类型"] == "Provider用量未关联请求"
    assert "未关联" in unassociated["说明"]
    assert "上下文原文" not in repr(result)


def test_human_csv_export_includes_requests_without_provider_usage():
    result = build_human_csv_export(
        [
            {
                "created_at": "2026-11-04T10:00:00+00:00",
                "kind": "request",
                "request_id": "trace-without-call",
                "session_hash": "session-hash",
                "details": {
                    "hook_changed": ["tools"],
                    "after": {"tools": "tools-hash:3"},
                    "handler_trace": [],
                },
            }
        ],
        hours=24,
        generated_at="2026-11-04T10:01:00+00:00",
        limited_to=10000,
    )

    assert len(result["rows"]) == 1
    assert result["rows"][0]["记录类型"] == "请求未见Provider用量"
    assert result["rows"][0]["请求字段变化"] == "tools"
