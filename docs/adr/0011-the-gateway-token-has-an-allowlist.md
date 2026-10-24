# ADR 0011 — Superset's own token opens an allowlist, not the engine

*Status: accepted.*

## Context

The extension's backend runs inside Superset. To ask the engine anything it must hold a token and name the
person signed in. For a while it held the engine's one token — which the engine accepts for anything — so the
rule that PROD needs a second person rested on the absence of forty lines in a file inside Superset. A second
reader found it the same hour it was committed ([`../lessons.md`](../lessons.md)).

## Decision

Superset holds a **token of its own**. The engine accepts it for an explicit list of routes
(`aibi/main.py::SUPERSET_MAY`) and refuses everything else — approvals, promotions, restores, the audit trail,
the tool layer, people. The list is what a developer DOES: the projects, the Studio and its draft, a
validation, a commit, a run, and the governed lists the explorer shows.

Who is asking is read from Superset's session, on the server, never from the request; the engine applies
`registry/security/rbac.yaml` to that person.

## Consequences

- A fault, another extension, or Superset itself cannot approve anything with that token.
- A test asks the engine's own routes which ones the token opens, and a second test holds the extension's
  source to asking exactly those — so the two cannot drift apart.
- Adding a Studio feature that needs a new route is a deliberate act: the route, the allowlist, the extension
  and both tests change together.
- Superset's account management is part of what SharpSight trusts: someone who can create a Superset account
  named like a developer can act as them. That was accepted, in writing, for exactly these actions.
