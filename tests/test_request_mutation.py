from types import SimpleNamespace

from astrbot_plugin_prompt_audit.request_mutation import (
    capture_request_material,
    capture_provider_request_snapshot,
    describe_handler_inventory,
    describe_request_mutation,
)


def _fingerprint(value):
    return f"hash-{hash(str(value))}"


def _delta(field, before, after):
    return describe_request_mutation(
        {field: before},
        {field: after},
        _fingerprint,
    )[field]


def test_text_delta_identifies_append_and_never_exposes_content():
    before = "stable persona"
    after = "stable persona plus dynamic instruction"

    result = _delta("system", before, after)

    assert result["kind"] == "text_appended"
    assert result["added_suffix_chars"] == len(after) - len(before)
    assert result["before_hash"] == _fingerprint(before)
    assert result["after_hash"] == _fingerprint(after)
    assert before not in repr(result)
    assert after not in repr(result)


def test_text_delta_identifies_prepend_and_wrap():
    assert _delta("prompt", "base", "dynamic base")["kind"] == "text_prepended"
    wrapped = _delta("system", "persona", "prefix persona suffix")
    assert wrapped["kind"] == "text_wrapped"
    assert wrapped["added_prefix_chars"] == len("prefix ")
    assert wrapped["added_suffix_chars"] == len(" suffix")


def test_text_delta_marks_ambiguous_extension_without_guessing_position():
    result = _delta("system", "ab", "abab")

    assert result["kind"] == "text_extension_position_ambiguous"


def test_initializing_empty_text_does_not_misreport_an_append():
    result = _delta("system", "", "new system text")

    assert result["kind"] == "text_initialized"
    assert result["added_suffix_chars"] == 0


def test_list_delta_identifies_added_and_removed_positions():
    appended = _delta("contexts", ["a", "b"], ["a", "b", "c"])
    prepended = _delta("tools", ["a", "b"], ["c", "a", "b"])
    inserted = _delta("extra", ["a", "b"], ["a", "c", "b"])
    removed = _delta("contexts", ["a", "b", "c"], ["a", "c"])
    initialized = _delta("tools", [], ["a"])
    cleared = _delta("tools", ["a"], [])

    assert appended["kind"] == "items_appended"
    assert appended["added_items"] == 1
    assert prepended["kind"] == "items_prepended"
    assert inserted["kind"] == "items_inserted_around_existing"
    assert removed["kind"] == "items_removed"
    assert removed["removed_items"] == 1
    assert initialized["kind"] == "items_initialized"
    assert cleared["kind"] == "items_cleared"
    assert _delta("tool_results", ["a"], ["a", "b"])["kind"] == "items_appended"


def test_capture_request_material_keeps_prompt_text_only_in_transient_memory():
    request = SimpleNamespace(
        system_prompt="private system prompt",
        prompt="private user prompt",
        contexts=[{"content": "private history"}],
        func_tool=None,
        extra_user_content_parts=[],
        image_urls=[],
        audio_urls=[],
        model="model-a",
    )

    material = capture_request_material(request, _fingerprint)
    safe_delta = describe_request_mutation(
        material,
        {**material, "system": "private system prompt plus addition"},
        _fingerprint,
    )

    assert material["system"] == "private system prompt"
    assert safe_delta["system"]["kind"] == "text_appended"
    assert "private user prompt" not in repr(safe_delta)
    assert "private history" not in repr(material["contexts"])


def test_provider_boundary_snapshot_contains_only_fingerprints_and_sizes():
    secret_prompt = "private provider prompt"
    snapshot = capture_provider_request_snapshot(
        {
            "system_prompt": "private system",
            "prompt": secret_prompt,
            "contexts": [{"content": "private context"}],
            "func_tool": None,
            "image_urls": ["private-image-url"],
            "audio_urls": [],
            "extra_user_content_parts": [],
            "tool_calls_result": SimpleNamespace(content="private tool result"),
            "model": "model-a",
            "tool_choice": "auto",
        },
        _fingerprint,
        {
            "Authorization": "Bearer top-secret-value",
            "X-Prompt-Hint": "private header prompt",
        },
    )

    assert snapshot["prompt"].endswith(f":{len(secret_prompt)}")
    assert "private" not in repr(snapshot)
    assert "top-secret-value" not in repr(snapshot)
    assert snapshot["headers"]["Authorization"]["value"] == "redacted"
    assert snapshot["headers"]["X-Prompt-Hint"]["chars"] == len(
        "private header prompt"
    )
    assert "tool_results" in snapshot


def test_handler_inventory_identifies_disabled_plugins_and_filtering():
    active_plugin = SimpleNamespace(name="active-plugin", activated=True)
    inactive_plugin = SimpleNamespace(name="inactive-plugin", activated=False)
    eligible = SimpleNamespace(
        handler_module_path="plugins.active",
        handler_name="inject",
        handler_full_name="plugins.active_inject",
        extras_configs={"priority": 20},
        enabled=True,
    )
    inactive = SimpleNamespace(
        handler_module_path="plugins.inactive",
        handler_name="inject",
        handler_full_name="plugins.inactive_inject",
        extras_configs={"priority": 10},
        enabled=True,
    )
    filtered = SimpleNamespace(
        handler_module_path="plugins.filtered",
        handler_name="inject",
        handler_full_name="plugins.filtered_inject",
        extras_configs={"priority": 0},
        enabled=True,
    )

    inventory = describe_handler_inventory(
        [eligible, inactive, filtered],
        [eligible],
        {
            "plugins.active": active_plugin,
            "plugins.inactive": inactive_plugin,
            "plugins.filtered": active_plugin,
        }.get,
    )

    assert [item["status"] for item in inventory] == [
        "eligible",
        "plugin_inactive",
        "plugin_filtered",
    ]
