# ADR 0014 — No build step, and no second React on the page

*Status: accepted.*

## Context

The Studio's page already carries Superset's own React, loaded to draw the top bar. A workspace could have
been built with React too — taking Superset's through module federation's share scope (react 17.0.2, antd
5.27.6, as the SQL Lab panel does) or bringing its own.

Both have a cost. Sharing the host's React couples the workspace to a release candidate's share scope. Bringing
another React means two roots on one page, and a build: the kit has no Node, which is why the extension's
panel is built on a developer's machine and its `dist/` is committed.

## Decision

The Studio is **plain ES modules and plain DOM**, served as they are in the repository. No framework, no
bundler, no committed build output.

## Consequences

- The kit still ships a workspace without needing Node, and there is nothing to keep in step with Superset's
  module federation.
- What a framework would have given — a virtual DOM — is not missed at this size: a redraw rebuilds a dozen
  tiles, and the two places where it would be felt are handled without one (a resize drag moves CSS variables
  and commits once; a text field commits on `change`).
- The files are named in `superset-addon/sharpsight_bi.py` (`STUDIO_FILES`), so nothing else in that folder is
  reachable from Superset.
- If the workspace grows past what plain DOM carries well, this is the first decision to revisit — and the
  split into `api.js` / `state.js` / `view.js` / `ui.js` is already the shape a framework would want.
