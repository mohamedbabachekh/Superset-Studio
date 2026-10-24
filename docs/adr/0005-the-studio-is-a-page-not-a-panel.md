# ADR 0005 — The Studio is a page of Superset's own, not an extension panel

*Status: accepted.*

## Context

The brief's visual builder (§46–60) needs a workspace: an explorer, a canvas, an inspector, a copilot and a
drawer. The extension system of Superset 6.1.0 contributes a panel to SQL Lab and nowhere else (ADR 0002), so
a workspace cannot be an extension panel. The alternatives were a separate application (another address,
another sign-in, another shell) or a fork.

A spike on 2026-10-19 (commit `9473f76`) showed a third way: a route of SharpSight's own, rendered with
**Superset's own page template** (`superset/spa.html`), under Superset's own application — which draws the top
bar — with the person's Superset session, theme and address.

## Decision

The Studio is served at `/sharpsight/studio/<project>/` by the add-on's Flask blueprint
(`studio/addon/sharpsight_bi.py`), on Superset's own page template. Its files are served as they are in the
repository. What it asks, it asks of the extension's API, which is where its permissions already are.

## Consequences

- One application, one sign-in, one theme. Nothing about it is a second product.
- It rests on three things of Superset's own: `superset/spa.html` and its blocks, `get_spa_template_context`,
  and Flask's `csrf_token()` in a template. All three are in `governance/registry/compatibility.yaml` as things to
  re-check at every upgrade.
- Superset's application is loaded whole, to draw its bar, and told to take only the height the bar needs; its
  own client-side navigation removes the page when the address leaves `/sharpsight/`.
- If the extension API later offers a full view, the Studio can move into it without changing anything below
  the page: the API, the draft and the engine are untouched by where the page is served from.
