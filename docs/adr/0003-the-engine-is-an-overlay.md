# ADR 0003 — The AI BI engine is an overlay the kit starts without

*Status: accepted.*

## Context

The kit (Superset, the drivers, the branding, the seeded connections, the scheduled refresh, the installer)
is shipped as one offline tarball and is used on its own by people who want a Superset workbench. The AI BI
engine is a governed pipeline with a warehouse role of its own, dbt, a browser, and a policy engine.

## Decision

The engine is an **overlay**: `engine/`, `docker-compose.ai-bi.yaml`, its own volumes and its own
configuration layer (`studio/addon/`). The kit's `docker-compose.yaml` is never edited by it. The kit starts
without the engine and knows nothing about it.

## Consequences

- A kit can be installed, upgraded and verified with no engine present; `verify-bundle.sh` and
  `rehearse-upgrade.sh` are about the kit alone.
- Everything the engine adds to Superset is added by a configuration layer that Superset loads through
  `SUPERSET_CONFIG_PATH`, mounted only by the overlay.
- The engine's image is still not in the offline bundle (`build-devkit-bundle.sh`); that is owed, not decided
  against.
- Two compose files must agree about ports, networks and volumes. They are read together at every change.
