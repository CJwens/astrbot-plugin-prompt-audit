from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


CAPTURED_TEXT_FIELDS = ("system", "prompt")
MAX_PROMPT_TEXT_CHARS = 100_000
MAX_CAPTURED_HANDLER_CHANGES = 32


def _bounded_text(value: Any) -> dict[str, Any]:
    text = value if isinstance(value, str) else str(value or "")
    return {
        "text": text[:MAX_PROMPT_TEXT_CHARS],
        "chars": len(text),
        "truncated": len(text) > MAX_PROMPT_TEXT_CHARS,
    }


def capture_prompt_fields(values: dict[str, Any]) -> dict[str, Any]:
    return {
        field: _bounded_text(values.get(field, ""))
        for field in CAPTURED_TEXT_FIELDS
    }


def capture_prompt_request(req: Any) -> dict[str, Any]:
    return capture_prompt_fields(
        {
            "system": getattr(req, "system_prompt", ""),
            "prompt": getattr(req, "prompt", ""),
        }
    )


class PromptCaptureBuffer:
    def __init__(
        self,
        *,
        request_id: str,
        model: str,
        initial: dict[str, Any],
    ) -> None:
        self.request_id = str(request_id)[:128]
        self.model = str(model or "provider-default")[:256]
        self.initial = initial
        self.mutations: list[dict[str, Any]] = []
        self.mutation_count = 0
        self.mutations_truncated = False

    def record_handler_change(
        self,
        *,
        sequence: int,
        plugin: str,
        handler: str,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> None:
        fields = {
            field: {
                "before": _bounded_text(before.get(field, "")),
                "after": _bounded_text(after.get(field, "")),
            }
            for field in CAPTURED_TEXT_FIELDS
            if before.get(field, "") != after.get(field, "")
        }
        if not fields:
            return
        self.mutation_count += 1
        if len(self.mutations) >= MAX_CAPTURED_HANDLER_CHANGES:
            self.mutations_truncated = True
            return
        self.mutations.append(
            {
                "sequence": int(sequence),
                "plugin": str(plugin or "unknown")[:256],
                "handler": str(handler or "unknown")[:256],
                "fields": fields,
            }
        )

    def finish(
        self,
        *,
        final: dict[str, Any],
        completion: str,
    ) -> dict[str, Any]:
        captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        text_values = [
            item
            for snapshot in (self.initial, final)
            for item in snapshot.values()
        ]
        text_values.extend(
            version
            for mutation in self.mutations
            for change in mutation["fields"].values()
            for version in (change["before"], change["after"])
        )
        return {
            "captured_at": captured_at,
            "request_id": self.request_id,
            "model": self.model,
            "completion": completion,
            "initial": self.initial,
            "mutations": self.mutations,
            "mutation_count": self.mutation_count,
            "mutations_truncated": self.mutations_truncated,
            "final": final,
            "text_truncated": any(
                item.get("truncated", False) for item in text_values
            ),
        }

    def __repr__(self) -> str:
        return (
            f"<PromptCaptureBuffer request_id={self.request_id!r} "
            "prompt_text=<redacted>>"
        )
