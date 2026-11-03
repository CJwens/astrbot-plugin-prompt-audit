from __future__ import annotations

from collections import OrderedDict
from time import monotonic
from typing import Any


class RequestAssociationRegistry:
    """Keep short-lived, privacy-safe request associations for Provider calls."""

    def __init__(self, *, max_entries: int = 512, ttl_seconds: float = 180.0) -> None:
        self.max_entries = max(1, max_entries)
        self.ttl_seconds = max(1.0, ttl_seconds)
        self._by_key: dict[str, tuple[dict[str, Any], float]] = {}
        self._by_request: OrderedDict[str, tuple[dict[str, Any], float]] = (
            OrderedDict()
        )

    def register(
        self,
        request_context: dict[str, Any],
        keys: list[str],
        *,
        now: float | None = None,
    ) -> None:
        current = monotonic() if now is None else now
        request_id = str(request_context.get("request_id", "unknown"))
        self._prune(current)
        self._by_request.pop(request_id, None)
        self._by_request[request_id] = (request_context, current)
        for key in keys:
            if key:
                self._by_key[key] = (request_context, current)
        while len(self._by_request) > self.max_entries:
            old_request_id, (old_context, _) = self._by_request.popitem(last=False)
            for key, (context, _) in list(self._by_key.items()):
                if context is old_context:
                    self._by_key.pop(key, None)

    def lookup(
        self,
        keys: list[str],
        *,
        now: float | None = None,
    ) -> tuple[dict[str, Any], int] | None:
        current = monotonic() if now is None else now
        self._prune(current)
        for key in keys:
            entry = self._by_key.get(key)
            if entry is None:
                continue
            context, created_at = entry
            self._by_key[key] = (context, current)
            age_ms = max(0, int((current - created_at) * 1000))
            return context, age_ms
        return None

    def _prune(self, now: float) -> None:
        expired = {
            id(context)
            for context, created_at in self._by_request.values()
            if now - created_at > self.ttl_seconds
        }
        if not expired:
            return
        for request_id, (context, _) in list(self._by_request.items()):
            if id(context) in expired:
                self._by_request.pop(request_id, None)
        for key, (context, _) in list(self._by_key.items()):
            if id(context) in expired:
                self._by_key.pop(key, None)
