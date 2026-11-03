import hashlib
import inspect
import json
import sqlite3
from contextvars import ContextVar
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, Provider, ProviderRequest
from astrbot.api.web import json_response, request
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .request_association import RequestAssociationRegistry
from .request_export import request_export_fields
from .routing_identity import build_routing_metadata

PLUGIN_NAME = "astrbot_plugin_prompt_audit"
MAX_SESSIONS = 512
MAX_EVENTS = 10000
RETENTION_DAYS = 30
ASSOCIATION_TTL_SECONDS = 180.0
NAME_LOOKUP_TTL_SECONDS = 300.0
_REQUEST_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "prompt_audit_request_context", default=None
)


def _stable_json(value: Any) -> str:
    def normalize(item: Any) -> Any:
        model_dump = getattr(item, "model_dump", None)
        if callable(model_dump):
            return normalize(model_dump(mode="json"))
        if isinstance(item, dict):
            return {str(key): normalize(val) for key, val in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(part) for part in item]
        if item is None or isinstance(item, (str, int, float, bool)):
            return item
        return {"type": f"{type(item).__module__}.{type(item).__qualname__}"}

    return json.dumps(
        normalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()[:16]


def _request_snapshot(req: ProviderRequest) -> dict[str, Any]:
    system_prompt = req.system_prompt or ""
    prompt = req.prompt or ""
    contexts = req.contexts or []
    tools = list(getattr(req.func_tool, "tools", ()) or ())
    tool_schemas = [
        {
            "name": getattr(tool, "name", ""),
            "description": getattr(tool, "description", ""),
            "parameters": getattr(tool, "parameters", {}),
        }
        for tool in tools
    ]
    extra_parts = req.extra_user_content_parts or []
    return {
        "model": req.model or "provider-default",
        "system": f"{_fingerprint(system_prompt)}:{len(system_prompt)}",
        "prompt": f"{_fingerprint(prompt)}:{len(prompt)}",
        "contexts": f"{_fingerprint(contexts)}:{len(contexts)}",
        "tools": f"{_fingerprint(tool_schemas)}:{len(tool_schemas)}",
        "extra": f"{_fingerprint(extra_parts)}:{len(extra_parts)}",
        "images": f"{_fingerprint(req.image_urls or [])}:{len(req.image_urls or [])}",
        "audios": f"{_fingerprint(req.audio_urls or [])}:{len(req.audio_urls or [])}",
    }


def _changed_fields(
    before: dict[str, Any], after: dict[str, Any]
) -> list[str]:
    return [key for key in after if before.get(key) != after[key]]


@star.register(
    PLUGIN_NAME,
    "CJwen",
    "Inspect provider-reported cache usage and LLM request fingerprint changes.",
    "0.14.1",
)
class PromptAuditPlugin(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self._previous_snapshots: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._wrapped_handlers: list[tuple[Any, Any, Any]] = []
        self._database = (
            Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME / "diagnostics.db"
        )
        self._instrumented_providers: dict[
            int, tuple[Provider, Any, Any, Any, Any]
        ] = {}
        self._request_associations = RequestAssociationRegistry(
            max_entries=MAX_SESSIONS,
            ttl_seconds=ASSOCIATION_TTL_SECONDS,
        )
        self._routing_name_cache: OrderedDict[
            str, tuple[float, dict[str, str]]
        ] = OrderedDict()
        context.register_web_api(
            f"/{PLUGIN_NAME}/dashboard-data",
            self.dashboard_data,
            ["GET"],
            "Read cache and prompt diagnostics for the plugin dashboard.",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/export-data",
            self.export_data,
            ["GET"],
            "Export bounded cache and prompt diagnostics without prompt contents.",
        )

    async def initialize(self) -> None:
        self._database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._database, timeout=5) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS diagnostic_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    session_hash TEXT NOT NULL,
                    details TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_diagnostic_events_created "
                "ON diagnostic_events(created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_diagnostic_events_kind_created "
                "ON diagnostic_events(kind, created_at DESC)"
            )
        self._instrument_registered_providers()
        logger.info(
            "LLM request fingerprint and provider-reported cache usage diagnostics "
            "enabled; prompt contents are not logged. Dashboard data retention: "
            "%s days, max %s events; request association TTL: %ss.",
            RETENTION_DAYS,
            MAX_EVENTS,
            int(ASSOCIATION_TTL_SECONDS),
        )

    @staticmethod
    def _association_key(label: str, value: Any) -> str:
        if value is None or value == "":
            return ""
        return f"{label}:{_fingerprint(str(value))}"

    def _request_association_keys(
        self,
        *,
        session_id: Any = None,
        conversation_id: Any = None,
    ) -> list[str]:
        keys = [
            self._association_key("session", session_id),
            self._association_key("conversation", conversation_id),
        ]
        return [key for key in keys if key]

    def _register_request_association(
        self, request_context: dict[str, Any]
    ) -> None:
        self._request_associations.register(
            request_context,
            list(request_context.get("association_keys", [])),
        )

    def _resolve_request_association(
        self,
        *,
        session_id: Any = None,
        conversation_id: Any = None,
    ) -> dict[str, Any] | None:
        current = _REQUEST_CONTEXT.get()
        if (
            isinstance(current, dict)
            and current.get("finalized")
            and current.get("request_id")
        ):
            return {
                **self._association_details(
                    current,
                    source="contextvar",
                    age_ms=0,
                )
            }

        lookup = self._request_associations.lookup(
            self._request_association_keys(
                session_id=session_id,
                conversation_id=conversation_id,
            )
        )
        if lookup is None:
            return None
        context, age_ms = lookup
        return self._association_details(
            context,
            source="session_or_conversation_registry",
            age_ms=age_ms,
        )

    @staticmethod
    def _association_details(
        request_context: dict[str, Any],
        *,
        source: str,
        age_ms: int,
    ) -> dict[str, Any]:
        mutations = request_context.get("mutations", [])
        return {
            "association_status": "associated",
            "association_source": source,
            "association_age_ms": max(0, int(age_ms)),
            "llm_request_id": str(
                request_context.get("request_id", "unknown")
            )[:128],
            "request_hook_changed": list(
                request_context.get("hook_changed", [])
            ),
            "request_hook_plugins": sorted(
                {
                    str(item.get("plugin", ""))
                    for item in mutations
                    if item.get("plugin")
                }
            ),
            "request_hook_handlers": sorted(
                {
                    str(item.get("handler", ""))
                    for item in mutations
                    if item.get("handler")
                }
            ),
            "request_mutation_count": len(mutations),
            "request_snapshot_fingerprint": _fingerprint(
                request_context.get("after", {})
            ),
            **{
                key: str(value)
                for key, value in request_context.get("routing", {}).items()
                if value is not None
            },
        }

    async def _enrich_routing_names(
        self,
        event: AstrMessageEvent,
        routing: dict[str, str],
    ) -> dict[str, str]:
        if routing.get("scope_type") != "group":
            return routing
        cache_key = routing.get("actor_hash") or routing.get("group_hash", "")
        if not cache_key:
            return routing
        now = monotonic()
        cached = self._routing_name_cache.get(cache_key)
        if cached is not None:
            expires_at, values = cached
            if expires_at > now:
                routing.update(
                    {
                        key: value
                        for key, value in values.items()
                        if value
                    }
                )
                self._routing_name_cache.move_to_end(cache_key)
                return routing
            self._routing_name_cache.pop(cache_key, None)

        group_getter = getattr(event, "get_group", None)
        if not callable(group_getter):
            return routing
        values: dict[str, str] = {}
        try:
            group = await group_getter()
        except Exception:
            group = None
        if group is not None:
            group_name = str(getattr(group, "group_name", "") or "").strip()
            if group_name:
                values["group_name"] = group_name[:128]
                values["group_name_source"] = "group_api"
            sender_id = ""
            try:
                sender_id = str(event.get_sender_id() or "")
            except Exception:
                pass
            for member in getattr(group, "members", None) or []:
                if str(getattr(member, "user_id", "")) != sender_id:
                    continue
                member_name = str(getattr(member, "nickname", "") or "").strip()
                if member_name:
                    values["user_name"] = member_name[:128]
                    values["user_name_source"] = "group_member_api"
                break
        self._routing_name_cache[cache_key] = (
            now + NAME_LOOKUP_TTL_SECONDS,
            values,
        )
        self._routing_name_cache.move_to_end(cache_key)
        while len(self._routing_name_cache) > MAX_SESSIONS:
            self._routing_name_cache.popitem(last=False)
        routing.update(values)
        return routing

    def _instrument_registered_providers(self) -> None:
        for provider in self.context.provider_manager.provider_insts:
            provider_key = id(provider)
            if provider_key in self._instrumented_providers:
                continue

            original_text_chat = provider.text_chat
            original_text_chat_stream = provider.text_chat_stream

            async def traced_text_chat(
                *args: Any,
                _provider=provider,
                _original=original_text_chat,
                **kwargs: Any,
            ):
                invocation_id = uuid4().hex
                try:
                    call_arguments = inspect.signature(_original).bind_partial(
                        *args, **kwargs
                    ).arguments
                except (TypeError, ValueError):
                    call_arguments = kwargs
                try:
                    model = call_arguments.get("model") or _provider.get_model()
                except Exception:
                    model = "unknown"
                request_metadata = {
                    "model": model,
                    "session_id": call_arguments.get("session_id"),
                    "conversation_id": call_arguments.get("conversation_id"),
                }
                request_metadata["association"] = (
                    self._resolve_request_association(
                        session_id=request_metadata["session_id"],
                        conversation_id=request_metadata["conversation_id"],
                    )
                )
                try:
                    response = await _original(*args, **kwargs)
                except Exception as error:
                    self._safely_record_provider_response(
                        _provider,
                        LLMResponse("err"),
                        invocation_id,
                        {
                            **request_metadata,
                            "provider_call_failed": True,
                            "error_type": type(error).__name__,
                        },
                    )
                    raise
                self._safely_record_provider_response(
                    _provider,
                    response,
                    invocation_id,
                    {**request_metadata, "final_response_missing": False},
                )
                return response

            async def traced_text_chat_stream(
                *args: Any,
                _provider=provider,
                _original=original_text_chat_stream,
                **kwargs: Any,
            ):
                invocation_id = uuid4().hex
                try:
                    call_arguments = inspect.signature(_original).bind_partial(
                        *args, **kwargs
                    ).arguments
                except (TypeError, ValueError):
                    call_arguments = kwargs
                try:
                    model = call_arguments.get("model") or _provider.get_model()
                except Exception:
                    model = "unknown"
                request_metadata = {
                    "model": model,
                    "session_id": call_arguments.get("session_id"),
                    "conversation_id": call_arguments.get("conversation_id"),
                }
                request_metadata["association"] = (
                    self._resolve_request_association(
                        session_id=request_metadata["session_id"],
                        conversation_id=request_metadata["conversation_id"],
                    )
                )
                final_response = None
                provider_error = None
                try:
                    async for response in _original(*args, **kwargs):
                        if not response.is_chunk:
                            final_response = response
                        yield response
                except Exception as error:
                    provider_error = error
                    raise
                finally:
                    self._safely_record_provider_response(
                        _provider,
                        final_response or LLMResponse("err"),
                        invocation_id,
                        {
                            **request_metadata,
                            "final_response_missing": final_response is None,
                            "provider_call_failed": provider_error is not None,
                            "error_type": (
                                type(provider_error).__name__
                                if provider_error is not None
                                else None
                            ),
                        },
                    )

            provider.text_chat = traced_text_chat
            provider.text_chat_stream = traced_text_chat_stream
            self._instrumented_providers[provider_key] = (
                provider,
                original_text_chat,
                original_text_chat_stream,
                traced_text_chat,
                traced_text_chat_stream,
            )

    def _safely_record_provider_response(
        self,
        provider: Provider,
        response: LLMResponse,
        invocation_id: str,
        request_metadata: dict[str, Any],
    ) -> None:
        try:
            self._record_provider_response(
                provider, response, invocation_id, request_metadata
            )
        except Exception as error:
            logger.warning(
                "Unable to record Provider usage provider_id=%s error_type=%s",
                provider.provider_config.get("id", "unknown"),
                type(error).__name__,
            )

    def _record_provider_response(
        self,
        provider: Provider,
        response: LLMResponse,
        invocation_id: str,
        request_metadata: dict[str, Any],
    ) -> None:
        provider_id = str(provider.provider_config.get("id", "unknown"))
        model = str(
            request_metadata.get("model")
            or provider.get_model()
            or provider.provider_config.get("model", "unknown")
        )
        session_id = (
            request_metadata.get("conversation_id")
            or request_metadata.get("session_id")
            or "unknown"
        )
        session_hash = _fingerprint(str(session_id))
        usage = response.usage
        response_id = getattr(response, "id", None) or "unknown"
        common_details = {
            "scope": "provider_call",
            "provider_id": provider_id,
            "model": model,
            "invocation_id": invocation_id,
            "response_id": response_id,
            "response_chunk": bool(getattr(response, "is_chunk", False)),
            "association_status": "unassociated",
        }
        association = request_metadata.get("association")
        if isinstance(association, dict):
            common_details.update(association)
        if request_metadata.get("provider_call_failed"):
            details = {
                **common_details,
                "status": "provider_call_failed",
                "error_type": request_metadata.get("error_type", "unknown"),
            }
            self._save_event("usage", invocation_id, session_hash, details)
            logger.warning(
                "LLM cache usage scope=provider_call invocation_id=%s "
                "provider_id=%s model=%s status=provider_call_failed "
                "error_type=%s",
                invocation_id,
                provider_id,
                model,
                details["error_type"],
            )
            return

        if request_metadata.get("final_response_missing"):
            details = {
                **common_details,
                "status": "final_response_missing",
            }
            self._save_event("usage", invocation_id, session_hash, details)
            logger.warning(
                "LLM cache usage scope=provider_call invocation_id=%s "
                "provider_id=%s model=%s status=final_response_missing",
                invocation_id,
                provider_id,
                model,
            )
            return

        if usage is None:
            self._save_event(
                "usage",
                invocation_id,
                session_hash,
                {**common_details, "status": "usage_unavailable"},
            )
            logger.warning(
                "LLM cache usage scope=provider_call invocation_id=%s "
                "provider_id=%s model=%s status=usage_unavailable",
                invocation_id,
                provider_id,
                model,
            )
            return

        try:
            input_cached = max(0, int(getattr(usage, "input_cached", 0) or 0))
            input_other = max(0, int(getattr(usage, "input_other", 0) or 0))
            output = max(0, int(getattr(usage, "output", 0) or 0))
            input_total = input_cached + input_other
            if input_total == 0:
                status = "no_input_tokens_reported"
                cache_ratio = None
            elif input_cached > 0:
                status = "cached_tokens_reported"
                cache_ratio = input_cached / input_total
            else:
                status = "zero_cached_tokens_reported"
                cache_ratio = 0.0
            details = {
                **common_details,
                "status": status,
                "input_cached": input_cached,
                "input_other": input_other,
                "input_total": input_total,
                "output": output,
                "cache_ratio": cache_ratio,
            }
            self._save_event("usage", invocation_id, session_hash, details)
            logger.info(
                "LLM cache usage scope=provider_call invocation_id=%s "
                "provider_id=%s model=%s status=%s input_cached=%s "
                "input_other=%s input_total=%s cache_ratio=%s output=%s "
                "association=%s association_source=%s mutation_count=%s",
                invocation_id,
                provider_id,
                model,
                status,
                input_cached,
                input_other,
                input_total,
                f"{cache_ratio:.4f}" if cache_ratio is not None else "unknown",
                output,
                common_details.get("association_status", "unassociated"),
                common_details.get("association_source", "none"),
                common_details.get("request_mutation_count", 0),
            )
        except (TypeError, ValueError, OverflowError) as error:
            self._save_event(
                "usage",
                invocation_id,
                session_hash,
                {
                    **common_details,
                    "status": "usage_parse_failed",
                    "error_type": type(error).__name__,
                },
            )
            logger.warning(
                "LLM cache usage scope=provider_call invocation_id=%s "
                "provider_id=%s status=usage_parse_failed error_type=%s",
                invocation_id,
                provider_id,
                type(error).__name__,
            )

    def _save_event(
        self,
        kind: str,
        request_id: str,
        session_hash: str,
        details: dict[str, Any],
    ) -> None:
        try:
            with sqlite3.connect(self._database, timeout=5) as connection:
                connection.execute(
                    """
                    INSERT INTO diagnostic_events
                        (created_at, kind, request_id, session_hash, details)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        kind,
                        str(request_id)[:128],
                        str(session_hash)[:128],
                        _stable_json(details),
                    ),
                )
                connection.execute(
                    """
                    DELETE FROM diagnostic_events
                    WHERE id NOT IN (
                        SELECT id FROM diagnostic_events
                        ORDER BY id DESC LIMIT ?
                    )
                    """,
                    (MAX_EVENTS,),
                )
                cutoff = (
                    datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
                ).isoformat(timespec="seconds")
                connection.execute(
                    "DELETE FROM diagnostic_events WHERE created_at < ?",
                    (cutoff,),
                )
        except sqlite3.Error as error:
            logger.warning(
                "Unable to persist prompt audit event kind=%s error_type=%s",
                kind,
                type(error).__name__,
            )

    async def dashboard_data(self):
        hours = request.query.get("hours", 24, type=int)
        hours = min(max(hours or 24, 1), RETENTION_DAYS * 24)
        page = request.query.get("page", 1, type=int)
        page = max(page or 1, 1)
        page_size = request.query.get("page_size", 50, type=int)
        page_size = min(max(page_size or 50, 10), 100)
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).isoformat(timespec="seconds")
        with sqlite3.connect(self._database, timeout=5) as connection:
            connection.row_factory = sqlite3.Row
            stored_events = connection.execute(
                """
                SELECT COUNT(*)
                FROM diagnostic_events
                WHERE created_at >= ?
                """,
                (cutoff,),
            ).fetchone()[0]
            total_events = min(stored_events, MAX_EVENTS)
            stats_rows = connection.execute(
                """
                SELECT created_at, kind, request_id, session_hash, details
                FROM diagnostic_events
                WHERE created_at >= ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (cutoff, MAX_EVENTS),
            ).fetchall()
            total_pages = max(
                1, (total_events + page_size - 1) // page_size
            )
            page = min(page, total_pages)
            page_rows = connection.execute(
                """
                SELECT created_at, kind, request_id, session_hash, details
                FROM diagnostic_events
                WHERE created_at >= ?
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (cutoff, page_size, (page - 1) * page_size),
            ).fetchall()
        events = [
            {
                "created_at": row["created_at"],
                "kind": row["kind"],
                "request_id": row["request_id"],
                "session_hash": row["session_hash"],
                "details": json.loads(row["details"]),
            }
            for row in page_rows
        ]
        usage = [
            json.loads(row["details"])
            for row in stats_rows
            if row["kind"] == "usage"
        ]
        comparable_usage = [
            item for item in usage if item.get("input_total", 0) > 0
        ]
        model_totals: dict[tuple[str, str], dict[str, int]] = {}
        for item in comparable_usage:
            key = (item.get("provider_id", "unknown"), item.get("model", "unknown"))
            totals = model_totals.setdefault(
                key, {"input_cached": 0, "input_other": 0, "calls": 0}
            )
            input_cached = int(item.get("input_cached", 0) or 0)
            input_other = int(item.get("input_other", 0) or 0)
            totals["input_cached"] += input_cached
            totals["input_other"] += input_other
            totals["calls"] += 1
            totals.setdefault("cached_calls", 0)
            totals.setdefault("zero_cached_calls", 0)
            totals.setdefault("hook_changed_calls", 0)
            totals.setdefault("unassociated_calls", 0)
            if input_cached > 0:
                totals["cached_calls"] += 1
            else:
                totals["zero_cached_calls"] += 1
            if item.get("request_hook_changed"):
                totals["hook_changed_calls"] += 1
            if item.get("association_status") != "associated":
                totals["unassociated_calls"] += 1
        scope_totals: dict[
            tuple[str, str, str, str, str, str], dict[str, int]
        ] = {}
        unattributed_usage = 0
        for item in usage:
            if item.get("status") in {
                "provider_call_failed",
                "final_response_missing",
                "usage_unavailable",
                "usage_parse_failed",
            }:
                continue
            input_total = int(item.get("input_total", 0) or 0)
            output = int(item.get("output", 0) or 0)
            if input_total <= 0 and output <= 0:
                continue
            provider_id = str(item.get("provider_id", "unknown"))
            model = str(item.get("model", "unknown"))
            scope_type = str(item.get("scope_type", "unknown"))
            group_hash = str(item.get("group_hash", ""))
            user_hash = str(item.get("user_hash", ""))
            scope_hash = str(item.get("scope_hash", ""))
            rollups: list[tuple[str, str, str, str, str]] = []
            if scope_type == "group" and group_hash:
                rollups.append(("group", group_hash, "", group_hash, ""))
                if user_hash:
                    rollups.append(
                        (
                            "group_member",
                            group_hash,
                            user_hash,
                            group_hash,
                            user_hash,
                        )
                    )
            elif scope_type == "private" and user_hash:
                rollups.append(
                    ("private_user", scope_hash, user_hash, "", user_hash)
                )
            else:
                unattributed_usage += 1
            for level, current_scope_hash, actor_hash, current_group_hash, current_user_hash in rollups:
                key = (
                    level,
                    current_scope_hash,
                    actor_hash,
                    provider_id,
                    model,
                    current_group_hash,
                )
                totals = scope_totals.setdefault(
                    key,
                    {
                        "input_cached": 0,
                        "input_other": 0,
                        "output": 0,
                        "calls": 0,
                        "group_name": "",
                        "user_name": "",
                        "cached_calls": 0,
                        "zero_cached_calls": 0,
                        "hook_changed_calls": 0,
                        "unassociated_calls": 0,
                    },
                )
                if item.get("group_name") and not totals["group_name"]:
                    totals["group_name"] = str(item["group_name"])[:128]
                if item.get("user_name") and not totals["user_name"]:
                    totals["user_name"] = str(item["user_name"])[:128]
                totals["input_cached"] += int(
                    item.get("input_cached", 0) or 0
                )
                totals["input_other"] += int(item.get("input_other", 0) or 0)
                totals["output"] += output
                totals["calls"] += 1
                if int(item.get("input_cached", 0) or 0) > 0:
                    totals["cached_calls"] += 1
                else:
                    totals["zero_cached_calls"] += 1
                if item.get("request_hook_changed"):
                    totals["hook_changed_calls"] += 1
                if item.get("association_status") != "associated":
                    totals["unassociated_calls"] += 1
                totals["user_hash"] = current_user_hash
        scope_rows = [
            {
                "level": level,
                "scope_type": (
                    "group"
                    if level == "group"
                    else "group_member"
                    if level == "group_member"
                    else "private"
                ),
                "scope_hash": scope_hash,
                "actor_type": (
                    "group"
                    if level == "group"
                    else "group_member"
                    if level == "group_member"
                    else "private_user"
                ),
                "actor_hash": actor_hash,
                "group_hash": group_hash,
                "user_hash": totals.get("user_hash", ""),
                "group_name": totals.get("group_name", ""),
                "user_name": totals.get("user_name", ""),
                "provider_id": provider_id,
                "model": model,
                **totals,
                "input_total": totals["input_cached"] + totals["input_other"],
                "reported_cache_ratio": (
                    totals["input_cached"]
                    / (totals["input_cached"] + totals["input_other"])
                    if totals["input_cached"] + totals["input_other"]
                    else None
                ),
            }
            for (
                level,
                scope_hash,
                actor_hash,
                provider_id,
                model,
                group_hash,
            ), totals in sorted(
                scope_totals.items(),
                key=lambda item: (
                    -item[1]["input_cached"]
                    - item[1]["input_other"],
                    -item[1]["output"],
                    item[0],
                ),
            )
        ]
        group_rows = [row for row in scope_rows if row["level"] == "group"]
        group_member_rows = [
            row for row in scope_rows if row["level"] == "group_member"
        ]
        private_rows = [
            row for row in scope_rows if row["level"] == "private_user"
        ]
        group_navigation: dict[str, dict[str, Any]] = {}
        for row in group_rows:
            current = group_navigation.setdefault(
                row["scope_hash"],
                {
                    "scope_hash": row["scope_hash"],
                    "group_name": row.get("group_name", ""),
                    "input_cached": 0,
                    "input_other": 0,
                    "output": 0,
                    "calls": 0,
                    "models": set(),
                },
            )
            current["input_cached"] += row["input_cached"]
            current["input_other"] += row["input_other"]
            current["output"] += row["output"]
            current["calls"] += row["calls"]
            if row.get("group_name") and not current.get("group_name"):
                current["group_name"] = row["group_name"]
            current["models"].add(
                f"{row['provider_id']} · {row['model']}"
            )
        private_navigation: dict[str, dict[str, Any]] = {}
        for row in private_rows:
            current = private_navigation.setdefault(
                row["user_hash"],
                {
                    "scope_hash": row["scope_hash"],
                    "user_hash": row["user_hash"],
                    "user_name": row.get("user_name", ""),
                    "input_cached": 0,
                    "input_other": 0,
                    "output": 0,
                    "calls": 0,
                    "models": set(),
                },
            )
            current["input_cached"] += row["input_cached"]
            current["input_other"] += row["input_other"]
            current["output"] += row["output"]
            current["calls"] += row["calls"]
            if row.get("user_name") and not current.get("user_name"):
                current["user_name"] = row["user_name"]
            current["models"].add(
                f"{row['provider_id']} · {row['model']}"
            )
        for navigation in (group_navigation, private_navigation):
            for item in navigation.values():
                item["input_total"] = (
                    item["input_cached"] + item["input_other"]
                )
                item["model_count"] = len(item.pop("models"))
        scope_view = request.query.get("scope_view", "all", type=str)
        scope_hash_filter = request.query.get(
            "scope_hash", "", type=str
        ).strip()
        if scope_view == "groups":
            visible_scope_rows = group_rows
        elif scope_view == "group_members":
            visible_scope_rows = [
                row
                for row in group_member_rows
                if not scope_hash_filter
                or row["group_hash"] == scope_hash_filter
            ]
        elif scope_view == "private":
            visible_scope_rows = private_rows
        else:
            visible_scope_rows = scope_rows
        scope_page = request.query.get("scope_page", 1, type=int)
        scope_page = max(scope_page or 1, 1)
        scope_page_size = request.query.get("scope_page_size", 20, type=int)
        scope_page_size = min(max(scope_page_size or 20, 10), 50)
        total_scope_rows = len(visible_scope_rows)
        total_scope_pages = max(
            1, (total_scope_rows + scope_page_size - 1) // scope_page_size
        )
        scope_page = min(scope_page, total_scope_pages)
        scope_start = (scope_page - 1) * scope_page_size
        return json_response(
            {
                "hours": hours,
                "events": events,
                "scope_usage": visible_scope_rows[
                    scope_start : scope_start + scope_page_size
                ],
                "scope_view": scope_view,
                "scope_navigation": {
                    "groups": sorted(
                        group_navigation.values(),
                        key=lambda item: (
                            -item["input_total"],
                            item["scope_hash"],
                        ),
                    )[:100],
                    "group_count": len(group_navigation),
                    "private_user_count": len(private_navigation),
                },
                "group_detail": self._build_group_detail(
                    scope_hash_filter,
                    group_rows,
                    group_member_rows,
                ),
                "summary": {
                    "usage_records": len(usage),
                    "comparable_records": len(comparable_usage),
                    "cached_records": sum(
                        item.get("input_cached", 0) > 0
                        for item in comparable_usage
                    ),
                    "zero_cached_records": sum(
                        int(item.get("input_cached", 0) or 0) == 0
                        for item in comparable_usage
                    ),
                    "hook_changed_usage_records": sum(
                        bool(item.get("request_hook_changed"))
                        for item in comparable_usage
                    ),
                    "associated_usage_records": sum(
                        item.get("association_status") == "associated"
                        for item in usage
                    ),
                    "unassociated_usage_records": sum(
                        item.get("association_status") != "associated"
                        for item in usage
                    ),
                    "models": [
                        {
                            "provider_id": provider_id,
                            "model": model,
                            **totals,
                            "input_total": totals["input_cached"]
                            + totals["input_other"],
                            "reported_cache_ratio": (
                                totals["input_cached"]
                                / (totals["input_cached"] + totals["input_other"])
                            ),
                        }
                        for (provider_id, model), totals in sorted(
                            model_totals.items()
                        )
                    ],
                    "missing_usage_records": sum(
                        item.get("status") == "usage_unavailable"
                        for item in usage
                    ),
                    "missing_final_records": sum(
                        item.get("status") == "final_response_missing"
                        for item in usage
                    ),
                    "failed_records": sum(
                        item.get("status") == "provider_call_failed"
                        for item in usage
                    ),
                    "mutations": sum(
                        row["kind"] == "mutation" for row in stats_rows
                    ),
                    "unattributed_usage_records": unattributed_usage,
                },
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total_events": total_events,
                    "total_pages": total_pages if total_events else 0,
                    "has_previous": page > 1 and total_events > 0,
                    "has_next": page < total_pages and total_events > 0,
                },
                "scope_pagination": {
                    "page": scope_page,
                    "page_size": scope_page_size,
                    "total_rows": total_scope_rows,
                    "total_pages": (
                        total_scope_pages if total_scope_rows else 0
                    ),
                    "has_previous": scope_page > 1 and total_scope_rows > 0,
                    "has_next": (
                        scope_page < total_scope_pages
                        and total_scope_rows > 0
                    ),
                },
            }
        )

    @staticmethod
    def _build_group_detail(
        group_hash: str,
        group_rows: list[dict[str, Any]],
        group_member_rows: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not group_hash:
            return None
        selected_groups = [
            row for row in group_rows if row.get("scope_hash") == group_hash
        ]
        selected_members = [
            row for row in group_member_rows if row.get("group_hash") == group_hash
        ]
        if not selected_groups:
            return None

        def aggregate(rows: list[dict[str, Any]]) -> dict[str, int | float | None]:
            input_cached = sum(int(row.get("input_cached", 0) or 0) for row in rows)
            input_other = sum(int(row.get("input_other", 0) or 0) for row in rows)
            input_total = input_cached + input_other
            return {
                "input_cached": input_cached,
                "input_other": input_other,
                "input_total": input_total,
                "output": sum(int(row.get("output", 0) or 0) for row in rows),
                "calls": sum(int(row.get("calls", 0) or 0) for row in rows),
                "cached_calls": sum(
                    int(row.get("cached_calls", 0) or 0) for row in rows
                ),
                "zero_cached_calls": sum(
                    int(row.get("zero_cached_calls", 0) or 0) for row in rows
                ),
                "hook_changed_calls": sum(
                    int(row.get("hook_changed_calls", 0) or 0) for row in rows
                ),
                "unassociated_calls": sum(
                    int(row.get("unassociated_calls", 0) or 0) for row in rows
                ),
                "reported_cache_ratio": (
                    input_cached / input_total if input_total else None
                ),
            }

        model_rows: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in selected_groups:
            model_rows.setdefault(
                (
                    str(row.get("provider_id", "unknown")),
                    str(row.get("model", "unknown")),
                ),
                [],
            ).append(row)
        member_rows: dict[str, list[dict[str, Any]]] = {}
        for row in selected_members:
            member_rows.setdefault(str(row.get("user_hash", "")), []).append(row)

        first = selected_groups[0]
        return {
            "group_hash": group_hash,
            "group_name": first.get("group_name", ""),
            **aggregate(selected_groups),
            "model_count": len(model_rows),
            "member_count": len(member_rows),
            "models": [
                {
                    "provider_id": provider_id,
                    "model": model,
                    **aggregate(rows),
                }
                for (provider_id, model), rows in sorted(model_rows.items())
            ],
            "members": [
                {
                    "user_hash": user_hash,
                    "user_name": rows[0].get("user_name", ""),
                    **aggregate(rows),
                }
                for user_hash, rows in sorted(
                    member_rows.items(),
                    key=lambda item: (
                        -sum(int(row.get("input_total", 0) or 0) for row in item[1]),
                        item[0],
                    ),
                )
            ],
        }

    async def export_data(self):
        hours = request.query.get("hours", 24, type=int)
        hours = min(max(hours or 24, 1), RETENTION_DAYS * 24)
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).isoformat(timespec="seconds")
        with sqlite3.connect(self._database, timeout=5) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT created_at, kind, request_id, session_hash, details
                FROM diagnostic_events
                WHERE created_at >= ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (cutoff, MAX_EVENTS),
            ).fetchall()

        columns = [
            "created_at",
            "kind",
            "request_id",
            "session_hash",
            "provider_id",
            "model",
            "status",
            "input_cached",
            "input_other",
            "input_total",
            "output",
            "cache_ratio",
            "request_hook_changed",
            "association_status",
            "association_source",
            "association_age_ms",
            "llm_request_id",
            "request_hook_plugins",
            "request_hook_handlers",
            "request_mutation_count",
            "request_snapshot_fingerprint",
            "scope_type",
            "scope_hash",
            "actor_type",
            "actor_hash",
            "group_hash",
            "group_name",
            "group_name_source",
            "user_hash",
            "user_name",
            "user_name_source",
            "platform_hash",
            "plugin",
            "handler",
            "changed",
            "previous_turn_changed",
        ]
        exported_rows = []
        for row in rows:
            details = json.loads(row["details"])
            request_fields = request_export_fields(details, _fingerprint)
            exported_rows.append(
                {
                    "created_at": row["created_at"],
                    "kind": row["kind"],
                    "request_id": row["request_id"],
                    "session_hash": row["session_hash"],
                    "provider_id": details.get("provider_id", ""),
                    "model": details.get("model", ""),
                    "status": details.get("status", ""),
                    "input_cached": details.get("input_cached", ""),
                    "input_other": details.get("input_other", ""),
                    "input_total": details.get("input_total", ""),
                    "output": details.get("output", ""),
                    "cache_ratio": details.get("cache_ratio", ""),
                    **request_fields,
                    "association_status": details.get(
                        "association_status", "unassociated"
                    ),
                    "association_source": details.get(
                        "association_source", ""
                    ),
                    "association_age_ms": details.get(
                        "association_age_ms", ""
                    ),
                    "llm_request_id": details.get("llm_request_id", ""),
                    "scope_type": details.get("scope_type", ""),
                    "scope_hash": details.get("scope_hash", ""),
                    "actor_type": details.get("actor_type", ""),
                    "actor_hash": details.get("actor_hash", ""),
                    "group_hash": details.get("group_hash", ""),
                    "group_name": details.get("group_name", ""),
                    "group_name_source": details.get("group_name_source", ""),
                    "user_hash": details.get("user_hash", ""),
                    "user_name": details.get("user_name", ""),
                    "user_name_source": details.get("user_name_source", ""),
                    "platform_hash": details.get("platform_hash", ""),
                    "plugin": details.get("plugin", ""),
                    "handler": details.get("handler", ""),
                    "changed": details.get("changed", []),
                    "previous_turn_changed": details.get(
                        "previous_turn_changed", []
                    ),
                }
            )
        return json_response(
            {
                "hours": hours,
                "columns": columns,
                "rows": exported_rows,
                "limited_to": MAX_EVENTS,
            }
        )

    @filter.on_astrbot_loaded()
    async def wrap_request_handlers(self) -> None:
        from astrbot.core.star.star_handler import (
            EventType,
            star_handlers_registry,
        )
        from astrbot.core.star.star import star_map

        for handler in star_handlers_registry.get_handlers_by_event_type(
            EventType.OnLLMRequestEvent
        ):
            if handler.handler_module_path.startswith(
                f"data.plugins.{PLUGIN_NAME}."
            ):
                continue
            original = handler.handler
            if getattr(original, "_prompt_audit_wrapper", False):
                continue
            plugin = star_map.get(handler.handler_module_path)
            plugin_name = plugin.name if plugin else handler.handler_module_path

            async def traced_handler(
                event: AstrMessageEvent,
                req: ProviderRequest,
                *args: Any,
                _original=original,
                _handler=handler,
                _plugin_name=plugin_name,
                **kwargs: Any,
            ) -> Any:
                before = _request_snapshot(req)
                try:
                    result = await _original(event, req, *args, **kwargs)
                finally:
                    after = _request_snapshot(req)
                    changed = _changed_fields(before, after)
                    if changed:
                        request_context = event.get_extra(
                            "_prompt_audit_request_context"
                        )
                        if not isinstance(request_context, dict):
                            request_context = {}
                        mutation = {
                            "plugin": _plugin_name,
                            "handler": _handler.handler_name,
                            "changed": changed,
                        }
                        mutations = request_context.setdefault("mutations", [])
                        if mutation not in mutations:
                            mutations.append(mutation)
                        event_request_id = str(
                            request_context.get(
                                "request_id",
                                getattr(
                                    getattr(event, "trace", None),
                                    "trace_id",
                                    "unknown",
                                ),
                            )
                        )
                        event_session_hash = str(
                            request_context.get(
                                "session_hash",
                                _fingerprint(
                                    getattr(
                                        event,
                                        "unified_msg_origin",
                                        "unknown",
                                    )
                                ),
                            )
                        )
                        self._save_event(
                            "mutation",
                            event_request_id,
                            event_session_hash,
                            {
                                **(
                                    request_context.get("routing", {})
                                    if isinstance(request_context, dict)
                                    else {}
                                ),
                                "plugin": _plugin_name,
                                "handler": _handler.handler_name,
                                "changed": changed,
                                "before": {
                                    key: before[key] for key in changed
                                },
                                "after": {key: after[key] for key in changed},
                            },
                        )
                        logger.info(
                            "LLM request hook mutation request_id=%s plugin=%s "
                            "handler=%s changed=%s before=%s after=%s",
                            getattr(
                                getattr(event, "trace", None),
                                "trace_id",
                                "unknown",
                            ),
                            _plugin_name,
                            _handler.handler_name,
                            ",".join(changed),
                            _stable_json(
                                {key: before[key] for key in changed}
                            ),
                            _stable_json(
                                {key: after[key] for key in changed}
                            ),
                        )
                return result

            traced_handler._prompt_audit_wrapper = True
            handler.handler = traced_handler
            self._wrapped_handlers.append((handler, original, traced_handler))

        logger.info(
            "Wrapped %s LLM request hooks for per-plugin mutation diagnostics.",
            len(self._wrapped_handlers),
        )

    async def terminate(self) -> None:
        for (
            provider,
            original_text_chat,
            original_text_chat_stream,
            traced_text_chat,
            traced_text_chat_stream,
        ) in self._instrumented_providers.values():
            if provider.text_chat is traced_text_chat:
                provider.text_chat = original_text_chat
            if provider.text_chat_stream is traced_text_chat_stream:
                provider.text_chat_stream = original_text_chat_stream
        self._instrumented_providers.clear()
        for handler, original, wrapper in self._wrapped_handlers:
            if handler.handler is wrapper:
                handler.handler = original
        logger.info(
            "Restored %s original LLM request hooks.",
            len(self._wrapped_handlers),
        )
        self._wrapped_handlers.clear()

    @filter.on_llm_request(priority=1000000000)
    async def capture_request_before_hooks(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        try:
            self._instrument_registered_providers()
            session_id = req.session_id or event.unified_msg_origin or "unknown"
            conversation_id = (
                req.conversation.cid if req.conversation else None
            )
            request_id = getattr(
                getattr(event, "trace", None), "trace_id", "unknown"
            )
            if not request_id or request_id == "unknown":
                request_id = uuid4().hex
            routing = await self._enrich_routing_names(
                event,
                build_routing_metadata(event),
            )
            request_context = {
                "request_id": request_id,
                "session_hash": _fingerprint(session_id),
                "routing": routing,
                "hook_changed": [],
                "mutations": [],
                "finalized": False,
                "association_keys": self._request_association_keys(
                    session_id=session_id,
                    conversation_id=conversation_id,
                ),
            }
            _REQUEST_CONTEXT.set(request_context)
            event.set_extra(
                "_prompt_audit_before",
                {
                    "request_id": request_id,
                    "session_hash": request_context["session_hash"],
                    "snapshot": _request_snapshot(req),
                },
            )
            event.set_extra(
                "_prompt_audit_request_context",
                request_context,
            )
        except Exception as error:
            logger.warning(
                "Unable to capture initial LLM request fingerprint: %s",
                type(error).__name__,
            )

    @filter.on_llm_request(priority=-1000000000)
    async def report_request_after_hooks(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        before = event.get_extra("_prompt_audit_before")
        if not isinstance(before, dict):
            logger.warning(
                "LLM request fingerprint comparison unavailable; "
                "another hook may have stopped propagation."
            )
            return

        try:
            after = _request_snapshot(req)
            initial = before["snapshot"]
            hook_changes = _changed_fields(initial, after)
            request_context = _REQUEST_CONTEXT.get()
            if isinstance(request_context, dict):
                request_context["hook_changed"] = hook_changes
                request_context["after"] = after
                request_context["finalized"] = True
                self._register_request_association(request_context)
            session_hash = before["session_hash"]
            previous = self._previous_snapshots.get(session_hash)
            turn_changes = (
                [
                    key
                    for key in after
                    if previous is not None and previous.get(key) != after[key]
                ]
                if previous is not None
                else []
            )
            self._previous_snapshots[session_hash] = after
            self._previous_snapshots.move_to_end(session_hash)
            while len(self._previous_snapshots) > MAX_SESSIONS:
                self._previous_snapshots.popitem(last=False)
            logger.info(
                "LLM request fingerprint request_id=%s session_hash=%s "
                "hook_changed=%s previous_turn_changed=%s "
                "before=%s after=%s",
                before["request_id"],
                session_hash,
                ",".join(hook_changes) if hook_changes else "none",
                (
                    ",".join(turn_changes)
                    if previous is not None and turn_changes
                    else "none" if previous is not None else "first_seen"
                ),
                _stable_json(initial),
                _stable_json(after),
            )
            self._save_event(
                "request",
                before["request_id"],
                session_hash,
                {
                    **(
                        request_context.get("routing", {})
                        if isinstance(request_context, dict)
                        else {}
                    ),
                    "hook_changed": hook_changes,
                    "mutation_plugins": sorted(
                        {
                            str(item.get("plugin", ""))
                            for item in (
                                request_context.get("mutations", [])
                                if isinstance(request_context, dict)
                                else []
                            )
                            if item.get("plugin")
                        }
                    ),
                    "mutation_handlers": sorted(
                        {
                            str(item.get("handler", ""))
                            for item in (
                                request_context.get("mutations", [])
                                if isinstance(request_context, dict)
                                else []
                            )
                            if item.get("handler")
                        }
                    ),
                    "mutation_chain": (
                        request_context.get("mutations", [])
                        if isinstance(request_context, dict)
                        else []
                    ),
                    "previous_turn_changed": turn_changes,
                    "previous_seen": previous is not None,
                    "before": initial,
                    "after": after,
                },
            )
        except Exception as error:
            logger.warning(
                "Unable to compare LLM request fingerprints: %s",
                type(error).__name__,
            )
        finally:
            event.set_extra("_prompt_audit_before", None)

    @filter.on_llm_response()
    async def clear_request_context(
        self, event: AstrMessageEvent, response: LLMResponse
    ) -> None:
        _REQUEST_CONTEXT.set(None)
        event.set_extra("_prompt_audit_request_context", None)

