from astrbot_plugin_prompt_audit.request_export import request_export_fields


def test_request_export_fields_supports_existing_request_event_schema():
    details = {
        "hook_changed": ["system", "tools"],
        "mutation_plugins": ["plugin_a", "plugin_b"],
        "mutation_handlers": ["handler_a", "handler_b"],
        "mutation_chain": [{"plugin": "plugin_a"}, {"plugin": "plugin_b"}],
        "after": {"system": "abc:10", "tools": "def:2"},
    }

    result = request_export_fields(details, lambda snapshot: "snapshot-hash")

    assert result == {
        "request_hook_changed": ["system", "tools"],
        "request_hook_plugins": ["plugin_a", "plugin_b"],
        "request_hook_handlers": ["handler_a", "handler_b"],
        "request_mutation_count": 2,
        "request_mutation_chain": [
            {"plugin": "plugin_a"},
            {"plugin": "plugin_b"},
        ],
        "request_handler_trace": [],
        "request_handler_trace_count": 0,
        "request_handler_trace_truncated": False,
        "request_handler_inventory": [],
        "request_handler_inventory_count": 0,
        "request_handler_inventory_truncated": False,
        "request_mutations_truncated": False,
        "provider_request_snapshot": {},
        "request_snapshot_fingerprint": "snapshot-hash",
    }


def test_request_export_fields_preserve_provider_association_schema():
    details = {
        "request_hook_changed": ["extra"],
        "request_hook_plugins": ["plugin_a"],
        "request_hook_handlers": ["handler_a"],
        "request_mutation_count": 1,
        "request_mutation_chain": [],
        "request_handler_trace": [],
        "request_snapshot_fingerprint": "existing-hash",
        "hook_changed": ["system"],
        "mutation_plugins": ["legacy_plugin"],
        "mutation_handlers": ["legacy_handler"],
    }

    result = request_export_fields(details, lambda snapshot: "new-hash")

    assert result == {
        "request_hook_changed": ["extra"],
        "request_hook_plugins": ["plugin_a"],
        "request_hook_handlers": ["handler_a"],
        "request_mutation_count": 1,
        "request_snapshot_fingerprint": "existing-hash",
        "request_mutation_chain": [],
        "request_handler_trace": [],
        "request_handler_trace_count": 0,
        "request_handler_trace_truncated": False,
        "request_handler_inventory": [],
        "request_handler_inventory_count": 0,
        "request_handler_inventory_truncated": False,
        "request_mutations_truncated": False,
        "provider_request_snapshot": {},
    }


def test_request_export_fields_leave_unavailable_request_metadata_blank():
    result = request_export_fields({}, lambda snapshot: "new-hash")

    assert result == {
        "request_hook_changed": [],
        "request_hook_plugins": [],
        "request_hook_handlers": [],
        "request_mutation_count": "",
        "request_mutation_chain": [],
        "request_handler_trace": [],
        "request_handler_trace_count": 0,
        "request_handler_trace_truncated": False,
        "request_handler_inventory": [],
        "request_handler_inventory_count": 0,
        "request_handler_inventory_truncated": False,
        "request_mutations_truncated": False,
        "provider_request_snapshot": {},
        "request_snapshot_fingerprint": "",
    }


def test_request_export_never_includes_optional_prompt_capture_text():
    result = request_export_fields(
        {
            "initial": {"system": {"text": "private system"}},
            "mutations": [
                {
                    "plugin": "plugin-a",
                    "fields": {
                        "system": {
                            "before": {"text": "before"},
                            "after": {"text": "after"},
                        }
                    },
                }
            ],
            "final": {"system": {"text": "private final"}},
        },
        lambda snapshot: "snapshot-hash",
    )

    assert "private" not in repr(result)
    assert "initial" not in result
    assert "mutations" not in result
    assert "final" not in result
