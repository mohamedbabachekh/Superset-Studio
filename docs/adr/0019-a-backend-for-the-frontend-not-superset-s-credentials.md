# ADR 0019 — The Studio talks to a backend for the frontend, never to Superset's credentials

*Status: accepted.*

## Context

The Studio runs in a browser, inside Superset. It needs the project, the draft, the registry, the proposals,
the pictures and the packages. The naive route is to give the page what it needs directly — Superset's API,
the engine's token, the warehouse. Each would put a credential in a browser.

## Decision

The page talks to **one API, shaped for it**: the extension's backend inside Superset
(`/extensions/sharpsight/bi/…`). That backend holds the engine's token, reads **who is asking** from
Superset's session on the server, and asks the engine for that person. The engine applies the registry's
rules and answers.

The page holds no secret, knows no address, and has exactly one way in.

## Consequences

- `GET /studio` returns everything the workspace draws in one call — the project, the template's constraints,
  the layout, each chart with its governance, the draft, the versions, the free slots, what the copilot can be
  asked. The page is not left to assemble a view from six endpoints.
- Adding a capability is a deliberate act in four places: the engine's route, its allowlist
  ([ADR 0011](0011-the-gateway-token-has-an-allowlist.md)), the extension's route, and both guard tests.
- Binary answers (a tile, a package) pass through the same gateway with the same identity, rather than being
  fetched from anywhere else.
- The page can be replaced — another surface, another framework — without touching the rules.
