"""Onyx API client – thin httpx wrapper with NDJSON stream transform."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from .const import LOGGER, MAX_NDJSON_LINE_BYTES


class OnyxError(Exception):
    """Base error for Onyx API issues."""


class OnyxAuthError(OnyxError):
    """Authentication failed (401/403)."""


class OnyxConnectionError(OnyxError):
    """Cannot reach the Onyx server."""


# ---------------------------------------------------------------------------
# Data classes for API responses
# ---------------------------------------------------------------------------


@dataclass
class PersonaSnapshot:
    """Minimal persona info returned by GET /agents."""

    id: int
    name: str
    description: str = ""
    system_prompt: str | None = None
    tools: list[ToolSnapshot] = field(default_factory=list)


@dataclass
class ToolSnapshot:
    """Minimal tool info."""

    id: int
    name: str
    display_name: str = ""
    description: str = ""


# ---------------------------------------------------------------------------
# Packet types used by tool-progress surfacing
# ---------------------------------------------------------------------------

# Onyx packet types that represent a tool starting work.
_TOOL_START_TYPES: dict[str, str] = {
    "search_tool_start": "Searching documents",
    "python_tool_start": "Running Python",
    "custom_tool_start": "Running custom tool",
    "open_url_start": "Opening URLs",
    "image_generation_start": "Generating image",
    "deep_research_plan_start": "Planning deep research",
    "research_agent_start": "Researching",
    "coding_agent_start": "Running coding agent",
    "bash_tool_start": "Running command",
    "memory_tool_start": "Accessing memory",
    "file_reader_start": "Reading file",
}


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class OnyxClient:
    """Async Onyx API client using HA's shared httpx client."""

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        base_url: str,
        api_token: str,
    ) -> None:
        # Normalise: strip trailing slash, ensure /api suffix.
        base = base_url.rstrip("/")
        if not base.endswith("/api"):
            base += "/api"
        self._base = base
        self._token = api_token
        self._client = httpx_client

    # -- helpers ------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self._base}{path}"

    async def _get(self, path: str, **params: Any) -> Any:
        try:
            resp = await self._client.get(
                self._url(path),
                headers=self._headers(),
                params=params or None,
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise OnyxConnectionError(f"Cannot reach Onyx: {exc}") from exc
        _raise_for_status(resp)
        return resp.json()

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        try:
            resp = await self._client.post(
                self._url(path),
                headers=self._headers(),
                json=body,
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise OnyxConnectionError(f"Cannot reach Onyx: {exc}") from exc
        _raise_for_status(resp)
        return resp.json()

    async def _delete(self, path: str) -> None:
        try:
            resp = await self._client.delete(
                self._url(path),
                headers=self._headers(),
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise OnyxConnectionError(f"Cannot reach Onyx: {exc}") from exc
        _raise_for_status(resp)

    # -- public API ---------------------------------------------------------

    async def async_list_personas(self) -> list[PersonaSnapshot]:
        """Return all visible personas (agents)."""
        data = await self._get(
            "/agents",
            page_num=0,
            page_size=1000,
            include_default=True,
        )
        # Response is PaginatedReturn: {"items": [...], "total_items": N}
        items = data if isinstance(data, list) else data.get("items", data)
        result: list[PersonaSnapshot] = []
        for item in items:
            tools = [
                ToolSnapshot(
                    id=t["id"],
                    name=t.get("name", ""),
                    display_name=t.get("display_name", ""),
                    description=t.get("description", ""),
                )
                for t in item.get("tools", [])
            ]
            result.append(
                PersonaSnapshot(
                    id=item["id"],
                    name=item["name"],
                    description=item.get("description", ""),
                    system_prompt=item.get("system_prompt"),
                    tools=tools,
                )
            )
        return result

    async def async_list_tools(self) -> list[ToolSnapshot]:
        """Return all available tools."""
        data = await self._get("/tool")
        items = data if isinstance(data, list) else data.get("items", data)
        return [
            ToolSnapshot(
                id=t["id"],
                name=t.get("name", ""),
                display_name=t.get("display_name", ""),
                description=t.get("description", ""),
            )
            for t in items
        ]

    async def async_create_chat_session(
        self, persona_id: int, description: str | None = None
    ) -> str:
        """Create a new chat session, return the session UUID string."""
        payload: dict[str, object] = {"persona_id": persona_id}
        if description:
            payload["description"] = description
        data = await self._post("/chat/create-chat-session", payload)
        return str(data["chat_session_id"])

    async def async_delete_chat_session(self, session_id: str) -> None:
        """Delete a chat session."""
        await self._delete(f"/chat/delete-chat-session/{session_id}")

    async def async_get_user_role(self) -> str:
        """Return the role of the authenticated user (admin / basic / etc.)."""
        data = await self._get("/get-user-role")
        return data.get("role", "unknown")

    async def async_send_chat_message_stream(
        self,
        *,
        chat_session_id: str,
        message: str,
        additional_context: str | None = None,
        allowed_tool_ids: list[int] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream an Onyx chat message, yielding raw parsed NDJSON dicts.

        Each dict has at minimum a ``"type"`` key (the Onyx packet type).
        Callers should use ``transform_onyx_stream`` for the HA delta mapping.
        """
        body: dict[str, Any] = {
            "chat_session_id": chat_session_id,
            "message": message,
            "stream": True,
        }
        if additional_context:
            body["additional_context"] = additional_context
        if allowed_tool_ids is not None:
            body["allowed_tool_ids"] = allowed_tool_ids

        try:
            async with self._client.stream(
                "POST",
                self._url("/chat/send-chat-message"),
                headers=self._headers(),
                json=body,
                timeout=httpx.Timeout(connect=15.0, read=300.0, write=15.0, pool=15.0),
            ) as resp:
                await _raise_for_status_stream(resp)
                async for raw_line in resp.aiter_lines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    if len(line) > MAX_NDJSON_LINE_BYTES:
                        LOGGER.warning(
                            "Dropping oversized NDJSON line (%d bytes)", len(line)
                        )
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        LOGGER.debug("Skipping non-JSON line: %.120s", line)
                        continue
                    yield obj
        except httpx.HTTPError as exc:
            raise OnyxConnectionError(
                f"Stream connection failed: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# NDJSON → HA delta transform
# ---------------------------------------------------------------------------


async def transform_onyx_stream(
    raw_stream: AsyncIterator[dict[str, Any]],
    *,
    show_tool_progress: bool = True,
    supports_thinking: bool = True,
) -> AsyncGenerator[dict[str, Any], None]:
    """Transform raw Onyx NDJSON dicts into HA ``AssistantContentDeltaDict``s.

    Yields dicts with optional keys ``role``, ``content``, ``thinking_content``.

    The first content-bearing packet opens the assistant ``role`` so that
    thinking + answer land in a single HA assistant message.
    """
    role_opened = False
    # Track which tool-start types we've already emitted progress for
    # (dedup within one turn).
    progress_emitted: set[str] = set()

    async for obj in raw_stream:
        # -- unwrap Packet envelope ({"placement": ..., "obj": {...}}) ------
        inner = obj.get("obj", obj)
        ptype: str = inner.get("type", "")

        # -- bare lines (no "type" key) ------------------------------------
        if not ptype:
            if "chat_session_id" in obj:
                # Bare CreateChatSessionID – absorb silently.
                continue
            if "error" in obj:
                raise OnyxError(str(obj["error"]))
            # Unknown bare line – skip.
            continue

        # -- reasoning → thinking_content -----------------------------------
        if ptype == "reasoning_start":
            if not role_opened:
                role_opened = True
                yield {"role": "assistant"}
            continue

        if ptype == "reasoning_delta":
            text = inner.get("reasoning", "")
            if text and supports_thinking:
                yield {"thinking_content": text}
            continue

        if ptype == "reasoning_done":
            continue

        # -- message → content ----------------------------------------------
        if ptype == "message_start":
            if not role_opened:
                role_opened = True
                yield {"role": "assistant"}
            continue

        if ptype == "message_delta":
            content = inner.get("content", "")
            if content:
                yield {"content": content}
            continue

        # -- tool progress → thinking_content (optional) --------------------
        if ptype in _TOOL_START_TYPES and show_tool_progress:
            if ptype not in progress_emitted:
                progress_emitted.add(ptype)
                label = _TOOL_START_TYPES[ptype]
                # Add tool-specific detail when available.
                if ptype == "custom_tool_start":
                    tool_name = inner.get("tool_name", "")
                    if tool_name:
                        label = f"Running tool: {tool_name}"
                elif ptype == "research_agent_start":
                    task = inner.get("research_task", "")
                    if task:
                        label = f"Researching: {task}"
                if not role_opened:
                    role_opened = True
                    yield {"role": "assistant"}
                if supports_thinking:
                    yield {"thinking_content": f"\n[{label}...]\n"}
            continue

        # -- control / terminal packets ------------------------------------
        if ptype == "stop":
            # Ensure we opened a role so HA doesn't get an empty result.
            if not role_opened:
                role_opened = True
                yield {"role": "assistant"}
                yield {"content": ""}
            return

        if ptype == "error":
            msg = inner.get("exception", inner.get("message", "Unknown Onyx error"))
            raise OnyxError(str(msg))

        # -- ignored ---------------------------------------------------------
        if ptype in (
            "section_end",
            "chat_heartbeat",
            "top_level_branching",
            "citation_info",
            "tool_call_debug",
            "tool_call_argument_delta",
            # Intermediate deltas (search queries, docs, python output, etc.)
            # are absorbed – only the *start* gets surfaced as progress.
            "search_tool_queries_delta",
            "search_tool_filter_delta",
            "search_tool_documents_delta",
            "open_url_urls",
            "open_url_documents",
            "python_tool_delta",
            "custom_tool_args",
            "custom_tool_delta",
            "image_generation_heartbeat",
            "image_generation_final",
            "file_reader_result",
            "memory_tool_delta",
            "memory_tool_no_access",
            "deep_research_plan_delta",
            "intermediate_report_start",
            "intermediate_report_delta",
            "intermediate_report_cited_docs",
            "coding_agent_thinking_delta",
            "coding_agent_final",
            "bash_tool_delta",
        ):
            continue

        # Catch-all for unknown types – log and skip.
        LOGGER.debug("Ignoring unknown Onyx packet type: %s", ptype)

    # Stream ended without a stop packet – still ensure role was opened.
    if not role_opened:
        yield {"role": "assistant"}
        yield {"content": ""}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raise_for_status(resp: httpx.Response) -> None:
    """Raise typed errors for HTTP failures (non-streaming responses)."""
    if resp.status_code in (401, 403):
        raise OnyxAuthError(
            f"Onyx authentication failed ({resp.status_code}): {resp.text[:200]}"
        )
    if resp.status_code >= 400:
        raise OnyxError(
            f"Onyx API error {resp.status_code}: {resp.text[:500]}"
        )


async def _raise_for_status_stream(resp: httpx.Response) -> None:
    """Raise typed errors for streaming responses (body not yet read)."""
    if resp.status_code < 400:
        return
    # Read a small chunk of the body for the error message.
    try:
        await resp.aread()
        body = resp.text[:500]
    except Exception:
        body = "(body unavailable)"
    if resp.status_code in (401, 403):
        raise OnyxAuthError(
            f"Onyx authentication failed ({resp.status_code}): {body}"
        )
    raise OnyxError(f"Onyx API error {resp.status_code}: {body}")
