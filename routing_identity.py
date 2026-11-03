from __future__ import annotations

import hashlib
from typing import Any


def _hash_identity(*parts: Any) -> str:
    value = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _safe_display_name(value: Any, *, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).replace("\x00", "").strip()
    if not text:
        return fallback
    return text[:128]


def _raw_message_value(event: Any, *keys: str) -> Any:
    raw_message = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if not isinstance(raw_message, dict):
        return None
    for key in keys:
        value = raw_message.get(key)
        if value not in (None, ""):
            return value
    return None


def _group_name(event: Any) -> tuple[str, str]:
    message_obj = getattr(event, "message_obj", None)
    group = getattr(message_obj, "group", None)
    candidates = (
        ("message_group", getattr(group, "group_name", None)),
        ("message_object", getattr(message_obj, "group_name", None)),
        ("event", getattr(event, "group_name", None)),
        ("raw_event", _raw_message_value(event, "group_name", "groupName")),
    )
    for source, value in candidates:
        name = _safe_display_name(value)
        if name:
            return name, source
    return "", "unavailable"


def _sender_name(event: Any) -> tuple[str, str]:
    message_obj = getattr(event, "message_obj", None)
    sender = getattr(message_obj, "sender", None)
    try:
        event_name = event.get_sender_name()
    except Exception:
        event_name = ""
    raw_sender = _raw_message_value(event, "sender")
    raw_name = ""
    if isinstance(raw_sender, dict):
        raw_name = raw_sender.get("card") or raw_sender.get("nickname") or ""
    candidates = (
        ("event", event_name),
        ("message_object", getattr(sender, "nickname", None)),
        ("raw_event", raw_name),
        ("raw_event", _raw_message_value(event, "sender_name", "senderName")),
    )
    for source, value in candidates:
        name = _safe_display_name(value)
        if name:
            return name, source
    return "", "unavailable"


def build_routing_metadata(event: Any) -> dict[str, str]:
    """Build privacy-safe routing dimensions from an AstrBot message event.

    Args:
        event: AstrBot message event exposing platform, group, and sender getters.

    Returns:
        Hashed dimensions used to aggregate usage by group and person.
    """
    try:
        platform = str(event.get_platform_name() or "")
    except Exception:
        platform = ""
    try:
        group_id = event.get_group_id()
    except Exception:
        group_id = None
    try:
        sender_id = event.get_sender_id()
    except Exception:
        sender_id = None
    sender_name, user_name_source = _sender_name(event)
    group_name, group_name_source = _group_name(event)

    metadata = {
        "scope_type": "unknown",
        "scope_hash": "",
        "actor_type": "unknown",
        "actor_hash": "",
        "group_hash": "",
        "user_hash": "",
        "platform_hash": _hash_identity("platform", platform) if platform else "",
        "group_name": group_name,
        "user_name": sender_name,
        "group_name_source": group_name_source,
        "user_name_source": user_name_source,
    }
    if group_id not in (None, ""):
        group_hash = _hash_identity("group", platform, group_id)
        metadata.update(
            {
                "scope_type": "group",
                "scope_hash": group_hash,
                "actor_type": "group_member",
                "group_hash": group_hash,
            }
        )
        if sender_id not in (None, ""):
            metadata["user_hash"] = _hash_identity(
                "user", platform, sender_id
            )
            metadata["actor_hash"] = _hash_identity(
                "group_member", platform, group_id, sender_id
            )
        return metadata

    if sender_id not in (None, ""):
        user_hash = _hash_identity("user", platform, sender_id)
        metadata.update(
            {
                "scope_type": "private",
                "scope_hash": _hash_identity(
                    "private", platform, sender_id
                ),
                "actor_type": "private_user",
                "actor_hash": user_hash,
                "user_hash": user_hash,
            }
        )
    return metadata
