# hass-onyx-conversation-agent

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mikew&repository=hass-onyx-conversation-agent)

A Home Assistant Conversation Agent backed by an [Onyx Agent](https://docs.onyx.app/developers/core_concepts#agents).

## Installation

Install using HACS by adding the repository `mikew/hass-onyx-conversation-agent`.

Restarts will be required after installation and any updates.

Once installed, visit `Settings > Devices & Services`, press `Add Integration`,
and search for `Onyx Conversation Agent`.

<img width="615" height="521" alt="Screenshot 2026-08-12 at 12 16 43 AM" src="https://github.com/user-attachments/assets/a17be8eb-e465-42b1-813d-d2018133b308" />

Enter the information for your Onyx instance and you're ready to create
Conversation Agents.

## Configuration

The way Home Assistant structures "conversation agents" is a multi-step process:

- Home Assistant has various "Assistants", one is included by default named "Home Assistant".
- Assistants are backed by "Conversation Agents", which are the actual implementations of the assistant.

First is to create the Conversation Agent, in `Settings > Devices & Services >
Onyx Conversation Agent`, tap `Add conversation agent`.

<img width="612" height="630" alt="Screenshot 2026-08-12 at 12 18 29 AM" src="https://github.com/user-attachments/assets/d04fda8d-05d7-4e00-8167-ea77aa21da47" />

Here you can set which Onyx Agent to use, along with other options.

<img width="612" height="630" alt="Screenshot 2026-08-12 at 12 18 09 AM" src="https://github.com/user-attachments/assets/34c1ad65-fe0c-4db4-bc76-554eedf7d365" />

Once that is finished, you now have to visit `Settings > Voice assistants`,
select an assistant, and in the `Conversation agent` section select your newly
created Conversation Agent.

<img width="612" height="537" alt="Screenshot 2026-08-12 at 12 29 51 AM" src="https://github.com/user-attachments/assets/0af2bf76-648b-465c-b7fc-d390e7885a5f" />
