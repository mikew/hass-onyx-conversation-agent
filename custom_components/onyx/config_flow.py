"""Config flow for Onyx integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_LLM_HASS_API
from homeassistant.core import callback
from homeassistant.helpers import llm
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_API_TOKEN,
    CONF_EXTRA_TOOL_IDS,
    CONF_LOCAL_FIRST,
    CONF_PERSONA_ID,
    CONF_SERVER_URL,
    CONF_SHOW_TOOL_PROGRESS,
    CONF_SYSTEM_PROMPT,
    DEFAULT_LOCAL_FIRST,
    DEFAULT_SHOW_TOOL_PROGRESS,
    DOMAIN,
    LOGGER,
)
from .onyx_client import OnyxAuthError, OnyxClient, OnyxConnectionError, OnyxError


# ---------------------------------------------------------------------------
# Main config flow – server entry
# ---------------------------------------------------------------------------


class OnyxConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Onyx."""

    VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls,
        _config_entry: ConfigEntry,
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {"conversation": OnyxConversationSubentryFlow}

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the initial server setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input[CONF_SERVER_URL].rstrip("/")
            token = user_input[CONF_API_TOKEN]

            client = OnyxClient(
                httpx_client=get_async_client(self.hass),
                base_url=url,
                api_token=token,
            )

            try:
                personas = await client.async_list_personas()
                LOGGER.debug(
                    "Onyx connection verified – %d persona(s) available",
                    len(personas),
                )
            except OnyxAuthError:
                errors["base"] = "invalid_auth"
            except OnyxConnectionError:
                errors["base"] = "cannot_connect"
            except OnyxError:
                errors["base"] = "unknown"
            except Exception:
                LOGGER.exception("Unexpected error connecting to Onyx")
                errors["base"] = "unknown"
            else:
                # Avoid duplicate entries for the same server.
                self._async_abort_entries_match({CONF_SERVER_URL: url})

                return self.async_create_entry(
                    title=f"Onyx ({url})",
                    data={
                        CONF_SERVER_URL: url,
                        CONF_API_TOKEN: token,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SERVER_URL): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.URL),
                    ),
                    vol.Required(CONF_API_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD),
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the server entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input[CONF_SERVER_URL].rstrip("/")
            token = user_input[CONF_API_TOKEN]

            client = OnyxClient(
                httpx_client=get_async_client(self.hass),
                base_url=url,
                api_token=token,
            )

            try:
                await client.async_list_personas()
            except OnyxAuthError:
                errors["base"] = "invalid_auth"
            except OnyxConnectionError:
                errors["base"] = "cannot_connect"
            except OnyxError:
                errors["base"] = "unknown"
            except Exception:
                LOGGER.exception("Unexpected error connecting to Onyx")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry=self._get_reconfigure_entry(),
                    title=f"Onyx ({url})",
                    data={
                        CONF_SERVER_URL: url,
                        CONF_API_TOKEN: token,
                    },
                )

        existing = self._get_reconfigure_entry().data
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_SERVER_URL): TextSelector(
                            TextSelectorConfig(type=TextSelectorType.URL),
                        ),
                        vol.Required(CONF_API_TOKEN): TextSelector(
                            TextSelectorConfig(type=TextSelectorType.PASSWORD),
                        ),
                    }
                ),
                existing,
            ),
            errors=errors,
        )


# ---------------------------------------------------------------------------
# Conversation subentry flow – per-persona agent setup
# ---------------------------------------------------------------------------


class OnyxConversationSubentryFlow(ConfigSubentryFlow):
    """Handle conversation subentry creation / reconfiguration."""

    async def _fetch_personas(self) -> list[SelectOptionDict]:
        """Fetch persona list from the Onyx server."""
        entry = self._get_entry()
        client: OnyxClient = entry.runtime_data  # type: ignore[assignment]
        try:
            personas = await client.async_list_personas()
            return [
                SelectOptionDict(
                    value=str(p.id),
                    label=p.name,
                )
                for p in personas
            ]
        except OnyxError as exc:
            LOGGER.warning("Failed to fetch Onyx personas: %s", exc)
            return []

    async def _fetch_tools(self) -> list[SelectOptionDict]:
        """Fetch available tools from the Onyx server."""
        entry = self._get_entry()
        client: OnyxClient = entry.runtime_data  # type: ignore[assignment]
        try:
            tools = await client.async_list_tools()
            return [
                SelectOptionDict(
                    value=str(t.id),
                    label=t.display_name or t.name,
                )
                for t in tools
            ]
        except OnyxError as exc:
            LOGGER.warning("Failed to fetch Onyx tools: %s", exc)
            return []

    def _get_llm_apis(self) -> list[SelectOptionDict]:
        return [
            SelectOptionDict(label=api.name, value=api.id)
            for api in llm.async_get_apis(self.hass)
        ]

    async def _build_schema(
        self,
        personas: list[SelectOptionDict] | None = None,
        tools: list[SelectOptionDict] | None = None,
    ) -> vol.Schema:
        if personas is None:
            personas = await self._fetch_personas()
        if tools is None:
            tools = await self._fetch_tools()

        llm_apis = self._get_llm_apis()

        schema: dict[vol.Marker, Any] = {
            vol.Required(CONF_PERSONA_ID): SelectSelector(
                SelectSelectorConfig(
                    options=personas,
                    mode=SelectSelectorMode.DROPDOWN,
                ),
            ),
            vol.Optional(CONF_SYSTEM_PROMPT, default=""): TemplateSelector(),
            vol.Optional(
                CONF_LLM_HASS_API,
                default=[llm.LLM_API_ASSIST],
            ): SelectSelector(
                SelectSelectorConfig(options=llm_apis, multiple=True),
            ),
            vol.Required(
                CONF_SHOW_TOOL_PROGRESS,
                default=DEFAULT_SHOW_TOOL_PROGRESS,
            ): bool,
            vol.Required(
                CONF_LOCAL_FIRST,
                default=DEFAULT_LOCAL_FIRST,
            ): bool,
        }

        if tools:
            schema[vol.Optional(CONF_EXTRA_TOOL_IDS, default=[])] = SelectSelector(
                SelectSelectorConfig(
                    options=tools,
                    multiple=True,
                    mode=SelectSelectorMode.DROPDOWN,
                ),
            )

        return vol.Schema(schema)

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> SubentryFlowResult:
        """Handle creation of a new conversation agent subentry."""
        if user_input is not None:
            # Convert persona_id back to int for storage.
            user_input[CONF_PERSONA_ID] = int(user_input[CONF_PERSONA_ID])
            if CONF_EXTRA_TOOL_IDS in user_input:
                user_input[CONF_EXTRA_TOOL_IDS] = [
                    int(x) for x in user_input[CONF_EXTRA_TOOL_IDS]
                ]
            # Filter out LLM APIs that no longer exist.
            if not user_input.get(CONF_LLM_HASS_API):
                user_input.pop(CONF_LLM_HASS_API, None)

            # Resolve persona name for the entry title.
            personas = await self._fetch_personas()
            pid = str(user_input[CONF_PERSONA_ID])
            name = next(
                (p["label"] for p in personas if p["value"] == pid),
                f"Persona {pid}",
            )
            server_title = self._get_entry().title
            return self.async_create_entry(
                title=f"{server_title}: {name}",
                data=user_input,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=await self._build_schema(),
        )

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> SubentryFlowResult:
        """Handle reconfiguration of an existing conversation agent subentry."""
        if user_input is not None:
            user_input[CONF_PERSONA_ID] = int(user_input[CONF_PERSONA_ID])
            if CONF_EXTRA_TOOL_IDS in user_input:
                user_input[CONF_EXTRA_TOOL_IDS] = [
                    int(x) for x in user_input[CONF_EXTRA_TOOL_IDS]
                ]
            if not user_input.get(CONF_LLM_HASS_API):
                user_input.pop(CONF_LLM_HASS_API, None)

            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                data=user_input,
            )

        existing = self._get_reconfigure_subentry().data.copy()
        # Convert int fields to str for form pre-population.
        if CONF_PERSONA_ID in existing:
            existing[CONF_PERSONA_ID] = str(existing[CONF_PERSONA_ID])
        if CONF_EXTRA_TOOL_IDS in existing:
            existing[CONF_EXTRA_TOOL_IDS] = [
                str(x) for x in existing[CONF_EXTRA_TOOL_IDS]
            ]

        # Filter out stale LLM APIs.
        llm_apis = [api.id for api in llm.async_get_apis(self.hass)]
        existing[CONF_LLM_HASS_API] = [
            api for api in existing.get(CONF_LLM_HASS_API, []) if api in llm_apis
        ]

        schema = self.add_suggested_values_to_schema(
            await self._build_schema(), existing
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
        )
