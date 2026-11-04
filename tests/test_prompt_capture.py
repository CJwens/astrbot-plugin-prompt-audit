from types import SimpleNamespace

from astrbot_plugin_prompt_audit.prompt_capture import (
    MAX_CAPTURED_HANDLER_CHANGES,
    MAX_PROMPT_TEXT_CHARS,
    PromptCaptureBuffer,
    capture_prompt_request,
)


def _snapshot(system, prompt):
    return {
        "system": {"text": system, "chars": len(system), "truncated": False},
        "prompt": {"text": prompt, "chars": len(prompt), "truncated": False},
    }


def test_capture_records_per_handler_text_and_final_text():
    initial = _snapshot("base system", "user message")
    buffer = PromptCaptureBuffer(
        request_id="request-1",
        model="model-a",
        initial=initial,
    )

    buffer.record_handler_change(
        sequence=3,
        plugin="prompt-plugin",
        handler="inject_prompt",
        before={"system": "base system", "prompt": "user message"},
        after={"system": "base system plus rules", "prompt": "user message"},
    )
    result = buffer.finish(
        final=_snapshot("base system plus rules", "user message"),
        completion="complete",
    )

    assert result["mutations"][0]["plugin"] == "prompt-plugin"
    assert result["mutations"][0]["fields"]["system"]["before"]["text"] == (
        "base system"
    )
    assert result["mutations"][0]["fields"]["system"]["after"]["text"] == (
        "base system plus rules"
    )
    assert result["final"]["system"]["text"] == "base system plus rules"
    assert result["completion"] == "complete"


def test_capture_ignores_unchanged_and_non_prompt_fields():
    buffer = PromptCaptureBuffer(
        request_id="request-2",
        model="model-a",
        initial=_snapshot("system", "prompt"),
    )

    buffer.record_handler_change(
        sequence=1,
        plugin="context-plugin",
        handler="append_context",
        before={"system": "system", "prompt": "prompt", "contexts": ["old"]},
        after={"system": "system", "prompt": "prompt", "contexts": ["new"]},
    )

    assert buffer.mutations == []
    assert buffer.mutation_count == 0


def test_capture_truncates_large_text_and_limits_handler_details():
    long_text = "x" * (MAX_PROMPT_TEXT_CHARS + 10)
    initial = capture_prompt_request(
        SimpleNamespace(system_prompt=long_text, prompt="hello")
    )
    buffer = PromptCaptureBuffer(
        request_id="request-3",
        model="model-a",
        initial=initial,
    )
    for sequence in range(MAX_CAPTURED_HANDLER_CHANGES + 1):
        buffer.record_handler_change(
            sequence=sequence,
            plugin=f"plugin-{sequence}",
            handler="inject",
            before={"system": "base", "prompt": ""},
            after={"system": f"base + {sequence}", "prompt": ""},
        )

    result = buffer.finish(
        final=capture_prompt_request(
            SimpleNamespace(system_prompt=long_text, prompt="hello")
        ),
        completion="complete",
    )

    assert len(result["initial"]["system"]["text"]) == MAX_PROMPT_TEXT_CHARS
    assert result["initial"]["system"]["truncated"] is True
    assert len(result["mutations"]) == MAX_CAPTURED_HANDLER_CHANGES
    assert result["mutation_count"] == MAX_CAPTURED_HANDLER_CHANGES + 1
    assert result["mutations_truncated"] is True
    assert result["text_truncated"] is True


def test_buffer_repr_never_discloses_prompt_text():
    buffer = PromptCaptureBuffer(
        request_id="request-4",
        model="model-a",
        initial=_snapshot("secret system text", "secret prompt text"),
    )

    assert "secret" not in repr(buffer)
    assert "redacted" in repr(buffer)
