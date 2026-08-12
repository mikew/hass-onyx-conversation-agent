"""The Onyx integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components.conversation import DOMAIN as CONVERSATION_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import service
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.typing import ConfigType

from .const import CONF_API_TOKEN, CONF_SERVER_URL, DOMAIN
from .onyx_client import OnyxAuthError, OnyxClient, OnyxConnectionError, OnyxError

PLATFORMS = [Platform.CONVERSATION]

type OnyxConfigEntry = ConfigEntry[OnyxClient]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register platform entity services."""
    service.async_register_platform_entity_service(
        hass=hass,
        service_domain=DOMAIN,
        service_name="new_session",
        entity_domain=CONVERSATION_DOMAIN,
        schema={
            vol.Optional("conversation_id"): str,
        },
        func=_handle_new_session,
    )
    service.async_register_platform_entity_service(
        hass=hass,
        service_domain=DOMAIN,
        service_name="delete_session",
        entity_domain=CONVERSATION_DOMAIN,
        schema={
            vol.Required("conversation_id"): str,
        },
        func=_handle_delete_session,
    )
    return True


async def _handle_new_session(entity: Entity, service_call: ServiceCall) -> None:
    """Service handler for DOMAIN.new_session."""
    conversation_id = service_call.data.get("conversation_id", "")
    if not conversation_id:
        # If no conversation_id, just create a standalone session.
        conversation_id = f"service-{service_call.context.id}"
    await entity.async_new_session(conversation_id)  # type: ignore[attr-defined]


async def _handle_delete_session(entity: Entity, service_call: ServiceCall) -> None:
    """Service handler for DOMAIN.delete_session."""
    conversation_id = service_call.data["conversation_id"]
    await entity.async_delete_session(conversation_id)  # type: ignore[attr-defined]


async def async_setup_entry(hass: HomeAssistant, entry: OnyxConfigEntry) -> bool:
    """Set up Onyx from a config entry."""
    client = OnyxClient(
        httpx_client=get_async_client(hass),
        base_url=entry.data[CONF_SERVER_URL],
        api_token=entry.data[CONF_API_TOKEN],
    )

    # Validate connectivity on startup.
    try:
        await client.async_list_personas()
    except OnyxAuthError as err:
        raise ConfigEntryError("Invalid Onyx API token") from err
    except OnyxConnectionError as err:
        raise ConfigEntryNotReady(f"Cannot reach Onyx server: {err}") from err
    except OnyxError as err:
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = client

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(
    hass: HomeAssistant,
    entry: OnyxConfigEntry,
) -> None:
    """Handle config entry updates."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: OnyxConfigEntry) -> bool:
    """Unload Onyx."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
