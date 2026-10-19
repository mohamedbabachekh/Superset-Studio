# ADR 0017 — One Superset, so promotion is a gate and a package

*Status: accepted.*

## Context

The brief (§32) prefers separate Superset environments for DEV, UAT and PROD. This kit has one Superset and
one warehouse, on one machine, and is installed by one command.

## Decision

Promotion is **a gate, a package and a record**, not a second server: a version moves through the ten quality
gates, is approved by a second person on the command line, and is cut as an immutable `.ssbi` package that a
deployment service can import elsewhere. The project's gate is shown in Superset as a tag.

## Consequences

- Nothing in this kit proves that an import into another Superset works; the package is built and verified,
  and the import is the deployment service's to do.
- A project standing in UAT or PROD accepts neither a request nor a commit: this kit has one BI engine, so a
  commit would rewrite the promoted dashboard in place. How such a project gets a next version is the owner's
  question, still open.
- The Studio shows the versions and where each stands, and does not promote.
