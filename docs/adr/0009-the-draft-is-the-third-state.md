# ADR 0009 — The draft is the third state, and carries a token

*Status: accepted.*

## Context

The brief (§50.2) names three states: the draft specification, the Superset assets, and the release manifest.
The engine had two — a committed version, and whatever Superset holds. A canvas needs the third: something
editable that is neither.

## Decision

A **draft** is kept with its project in the engine's own store: the specification a person is editing, saved
a moment after each change. It carries a **token** — a fingerprint of the draft it is a change to — and a save
presenting an older token is refused with what to do about it (§50.3), never merged silently.

A draft changes nothing in Superset by existing.

## Consequences

- Two people with the page open cannot overwrite each other without being told.
- The draft is an ordinary field of the project record: it survives a restart, and it is the engine's, not the
  browser's.
- Every proposal from the copilot or the gallery is applied by saving a draft — one road in, checked once.
- Discarding a draft is one call and leaves the committed version untouched.
