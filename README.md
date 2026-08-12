# hass-onyx-conversation-agent

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mikew&repository=hass-onyx-conversation-agent)

A Home Assistant Conversation Agent backed by an [Onyx Agent](https://docs.onyx.app/developers/core_concepts#agents).

## Installation

Install using HACS by adding the repository `mikew/hass-onyx-conversation-agent`.

Restarts will be required after installation and any updates.

Once installed, visit Settings > Devices & Services > Add Integration and
search for "Onyx Conversation Agent".

Enter the information for your Onyx instance and you're ready to create
Conversation Agents.

## Configuration

The way Home Assistant structures "conversation agents" is a multi-step process:

- Home Assistant has various "Assistants", one is included by default named "Home Assistant".
- Assistants are backed by "Conversation Agents", which are the actual implementations of the assistant.

First is to create the Conversation Agent, in `Settings > Devices & Services >
Onyx Conversation Agent`, tap `Add conversation agent`.

[screenshot]

Here you can set which Onyx Agent to use, along with other options.

[screenshot]

Once that is finished, you now have to visit `Settings > Voice assistants`,
select an assistant, and in the `Conversation agent` section select your newly
created Conversation Agent.

[screenshot]
