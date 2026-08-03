"""Conversation entity for Onyx."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from homeassistant.components import conversation
from homeassistant.const import CONF_LLM_HASS_API, MATCH_ALL
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, intent
from homeassistant.helpers.storage import Store

from .const import (
    CONF_EXTRA_TOOL_IDS,
    CONF_LOCAL_FIRST,
    CONF_PERSONA_ID,
    CONF_SHOW_TOOL_PROGRESS,
    CONF_SYSTEM_PROMPT,
    DEFAULT_LOCAL_FIRST,
    DEFAULT_SHOW_TOOL_PROGRESS,
    DOMAIN,
    LOGGER,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .onyx_client import OnyxClient, OnyxError, transform_onyx_stream

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry, ConfigSubentry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

# Check if HA supports thinking_content (2026.4+)
_SUPPORTS_THINKING = "thinking_content" in getattr(
    conversation.AssistantContent,
    "__dataclass_fields__",
    {},
)


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Onyx conversation entities from subentries."""
    for subentry_id, subentry in config_entry.subentries.items():
        if subentry.subentry_type != "conversation":
            continue
        async_add_entities(
            [OnyxConversationEntity(config_entry, subentry)],
            config_subentry_id=subentry_id,
        )


# ---------------------------------------------------------------------------
# Session store – persists conversation_id → onyx_session_id mapping
# Shared across all entities via hass.data[DOMAIN]["session_store"].
# ---------------------------------------------------------------------------

_SESSION_STORE_KEY = "session_store"


class _SessionStore:
    """Wraps HA's Store for the conversation -> Onyx session map."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, str]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY
        )
        self._data: dict[str, str] = {}
        self._loaded = False

    async def async_load(self) -> None:
        if self._loaded:
            return
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self._data = stored
        self._loaded = True

    def get(self, conversation_id: str) -> str | None:
        return self._data.get(conversation_id)

    def put(self, conversation_id: str, onyx_session_id: str) -> None:
        self._data[conversation_id] = onyx_session_id

    def remove(self, conversation_id: str) -> str | None:
        return self._data.pop(conversation_id, None)

    async def async_save(self) -> None:
        await self._store.async_save(self._data)


async def _get_shared_session_store(hass: HomeAssistant) -> _SessionStore:
    """Return the shared session store, creating it on first access."""
    domain_data: dict = hass.data.setdefault(DOMAIN, {})
    store: _SessionStore | None = domain_data.get(_SESSION_STORE_KEY)
    if store is None:
        store = _SessionStore(hass)
        domain_data[_SESSION_STORE_KEY] = store
    await store.async_load()
    return store


# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------


class OnyxConversationEntity(conversation.ConversationEntity):
    """Onyx conversation agent."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supports_streaming = True

    def __init__(
        self,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
    ) -> None:
        self._entry = entry
        self._subentry = subentry

        # Unique / device IDs follow the local_openai pattern.
        self._attr_unique_id = subentry.subentry_id
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="Onyx",
            entry_type=dr.DeviceEntryType.SERVICE,
        )

        # If HA LLM APIs are configured, advertise CONTROL feature.
        if subentry.data.get(CONF_LLM_HASS_API):
            self._attr_supported_features = (
                conversation.ConversationEntityFeature.CONTROL
            )

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return MATCH_ALL

    # -- helpers --

    @property
    def _client(self) -> OnyxClient:
        return self._entry.runtime_data  # type: ignore[return-value]

    async def _get_or_create_onyx_session(
        self, conversation_id: str
    ) -> str:
        """Return the Onyx chat_session_id for *conversation_id*, creating if needed."""
        store = await _get_shared_session_store(self.hass)
        onyx_id = store.get(conversation_id)
        if onyx_id is not None:
            return onyx_id

        persona_id: int = self._subentry.data[CONF_PERSONA_ID]
        onyx_id = await self._client.async_create_chat_session(persona_id)
        store.put(conversation_id, onyx_id)
        await store.async_save()
        LOGGER.debug(
            "Created Onyx session %s for conversation %s (persona %d)",
            onyx_id,
            conversation_id,
            persona_id,
        )
        return onyx_id

    # -- public API for services --

    async def async_new_session(self, conversation_id: str) -> str:
        """Force a new Onyx session for *conversation_id* (drops old mapping)."""
        store = await _get_shared_session_store(self.hass)
        old = store.remove(conversation_id)
        if old:
            try:
                await self._client.async_delete_chat_session(old)
            except OnyxError:
                LOGGER.warning("Failed to delete old Onyx session %s", old)
        persona_id: int = self._subentry.data[CONF_PERSONA_ID]
        new_id = await self._client.async_create_chat_session(persona_id)
        store.put(conversation_id, new_id)
        await store.async_save()
        return new_id

    async def async_delete_session(self, conversation_id: str) -> None:
        """Delete the Onyx session for *conversation_id*."""
        store = await _get_shared_session_store(self.hass)
        old = store.remove(conversation_id)
        if old:
            try:
                await self._client.async_delete_chat_session(old)
            except OnyxError:
                LOGGER.warning("Failed to delete Onyx session %s", old)
        await store.async_save()

    # -- main conversation handler --

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Process user input through Onyx (with optional local-first fallback)."""
        options = self._subentry.data

        # Provide LLM data (system prompt, tools) to the chat log.
        llm_apis = options.get(CONF_LLM_HASS_API) or None
        try:
            await chat_log.async_provide_llm_data(
                user_input.as_llm_context(DOMAIN),
                llm_apis,
                None,  # user_llm_prompt — we inject via additional_context instead
                user_input.extra_system_prompt,
            )
        except conversation.ConverseError as err:
            return err.as_conversation_result()

        # -- Local-first: try HA's built-in intent recognition for control --
        local_first = options.get(CONF_LOCAL_FIRST, DEFAULT_LOCAL_FIRST)
        if local_first:
            result = await self._try_local_intent(user_input)
            if result is not None:
                return result

        # -- Onyx path --
        return await self._handle_onyx(user_input, chat_log)

    async def _try_local_intent(
        self,
        user_input: conversation.ConversationInput,
    ) -> conversation.ConversationResult | None:
        """Try HA's built-in default agent for intent matching.

        Returns ``None`` on NO_INTENT_MATCH so the caller falls through to Onyx.
        Uses the public ``conversation.async_converse`` API with the default
        agent, which is the supported way to invoke intent processing.
        """
        try:
            # Use the public API to invoke HA's default conversation agent.
            result = await conversation.async_converse(
                hass=self.hass,
                text=user_input.text,
                conversation_id=None,  # Ephemeral — don't pollute user's chat log
                context=user_input.context,
                language=user_input.language,
                agent_id=conversation.HOME_ASSISTANT_AGENT,
            )
        except Exception:
            LOGGER.debug("Local agent failed, falling through to Onyx", exc_info=True)
            return None

        if (
            result.response.error_code
            == intent.IntentResponseErrorCode.NO_INTENT_MATCH
        ):
            LOGGER.debug("Local agent returned NO_INTENT_MATCH, routing to Onyx")
            return None

        return result

    async def _handle_onyx(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Stream Onyx response into the chat log."""
        options = self._subentry.data

        conversation_id = chat_log.conversation_id
        onyx_session_id = await self._get_or_create_onyx_session(conversation_id)

        system_prompt = (options.get(CONF_SYSTEM_PROMPT) or "").strip() or None
        show_tool_progress = options.get(
            CONF_SHOW_TOOL_PROGRESS, DEFAULT_SHOW_TOOL_PROGRESS
        )

        # Build allowed_tool_ids if extra tool IDs configured.
        extra_ids: list[int] = options.get(CONF_EXTRA_TOOL_IDS) or []
        allowed_tool_ids: list[int] | None = None
        if extra_ids:
            # Merge persona tools + extra. We need persona tool IDs;
            # fetch them from the client.
            persona_id: int = options[CONF_PERSONA_ID]
            try:
                personas = await self._client.async_list_personas()
                persona = next(
                    (p for p in personas if p.id == persona_id), None
                )
                persona_tool_ids = (
                    [t.id for t in persona.tools] if persona else []
                )
            except OnyxError:
                persona_tool_ids = []
            allowed_tool_ids = list(set(persona_tool_ids + extra_ids))

        try:
            raw_stream = self._client.async_send_chat_message_stream(
                chat_session_id=onyx_session_id,
                message=user_input.text,
                additional_context=system_prompt,
                allowed_tool_ids=allowed_tool_ids,
            )
            delta_stream = transform_onyx_stream(
                raw_stream,
                show_tool_progress=show_tool_progress,
                supports_thinking=_SUPPORTS_THINKING,
            )
            async for _content in chat_log.async_add_delta_content_stream(
                self.entity_id,
                delta_stream,
            ):
                pass  # HA accumulates content internally
        except OnyxError as exc:
            LOGGER.error("Onyx error: %s", exc)
            raise HomeAssistantError(f"Onyx error: {exc}") from exc

        return conversation.async_get_result_from_chat_log(user_input, chat_log)
