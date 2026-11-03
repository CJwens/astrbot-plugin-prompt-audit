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
        "request_snapshot_fingerprint": "snapshot-hash",
    }


def test_request_export_fields_preserve_provider_association_schema():
    details = {
        "request_hook_changed": ["extra"],
        "request_hook_plugins": ["plugin_a"],
        "request_hook_handlers": ["handler_a"],
        "request_mutation_count": 1,
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
    }


def test_request_export_fields_leave_unavailable_request_metadata_blank():
    result = request_export_fields({}, lambda snapshot: "new-hash")

    assert result == {
        "request_hook_changed": [],
        "request_hook_plugins": [],
        "request_hook_handlers": [],
        "request_mutation_count": "",
        "request_snapshot_fingerprint": "",
    }
