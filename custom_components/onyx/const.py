"""Constants for the Onyx integration."""

from __future__ import annotations

import logging

DOMAIN = "onyx"
LOGGER = logging.getLogger(__package__)

# ── Server-level config keys (config entry data) ──
CONF_SERVER_URL = "server_url"
CONF_API_TOKEN = "api_token"

# ── Agent subentry config keys ──
CONF_PERSONA_ID = "persona_id"
CONF_SYSTEM_PROMPT = "system_prompt"
CONF_SHOW_TOOL_PROGRESS = "show_tool_progress"
CONF_EXTRA_TOOL_IDS = "extra_tool_ids"

# ── Defaults ──
DEFAULT_SHOW_TOOL_PROGRESS = True

# ── Session store ──
STORAGE_KEY = f"{DOMAIN}.sessions"
STORAGE_VERSION = 1

# ── Misc ──
# Maximum NDJSON line length before we drop it (safety valve).
MAX_NDJSON_LINE_BYTES = 1_048_576  # 1 MiB
