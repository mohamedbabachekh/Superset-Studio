# ADR 0010 — A commit goes through the pipeline; nothing in Superset approves

*Status: accepted.*

## Context

The Studio could have written a version itself: it has the specification, and the engine has a store. It would
have been faster and it would have been a second road to production, with its own bugs and its own gaps.

## Decision

Committing a draft calls the **same pipeline** every other commit calls (`aibi/pipeline.py::_run_edited`): the
ten quality gates, the specification validated before Superset is called, the dashboard built, the data read
back, the dashboard looked at in a browser, the version stored, the git commit, the `.ssbi` package.

**Nothing in Superset approves or promotes.** Approving for UAT or PROD stays on the command line, done by
someone other than the person who asked for the version.

## Consequences

- There is one road to a version, and it is the road that is tested.
- A commit from the Studio is as slow as a commit from the command line, because it is the same work.
- The rule that PROD needs a second person cannot be gone round from inside Superset — and it is enforced by
  the engine's own allowlist (ADR 0011), not by the absence of a button.
