# ADR 0024 — A deployment carries the package that was validated, and is not done until it is read back

*Status: accepted.*

## Context

[ADR 0017](0017-one-superset-so-promotion-is-a-gate.md) settled what promotion means in this kit: there is
one BI engine, so UAT and PROD are **gates on a version**, not places — and what a gate produces is a `.ssbi`
package, Superset's own export of the dashboard taken at the moment the version was committed, beside the
specification, the governed datasets and metrics, the dbt it was built with, and the validation that passed.

The owner then asked the obvious next thing: *create a dashboard here and deploy it to the run platform, and
everything works without any issue.* That is a second Superset — a real one, somebody else's server — and
five things we had already paid for separately say how it has to be done:

- an import **keeps an existing connection's URI**, so a run environment quietly reads the development
  warehouse unless the connection is repointed afterwards (`docs/lessons.md`);
- a dashboard's **audience is not in an export**: roles and row-level rules travel separately;
- Superset 6 records a **catalog per dataset**, and for PostgreSQL the catalog is the database — so the
  governed connection must be called, there, what the package expects;
- an import **answers 200 for a dashboard whose charts cannot draw**;
- a package built against one Superset is not assumed to fit another.

## Decision

A deployment is **mechanical, and nothing is generated again**. No model is asked, no layout is computed, no
chart is rebuilt: the file that was validated is the file that ships. `aibi/deploy.py` runs nine steps, in
this order, and records every one of them:

read the package and check its checksum · check the target's versions against the matrix · point the governed
connection at that environment · import the dashboard, its charts and its datasets · **repoint** the imported
connection · grant the audience and the row-level rules SharpSight decided · read back what arrived · **ask
every chart for its data** · record the deployment.

Three rules around it:

- **It does not approve.** A version reaches a target already approved for that gate, by somebody other than
  whoever asked for it (`lifecycle.promote`). UAT takes a version approved for UAT or beyond; PROD takes one
  approved for PROD. An unvalidated version, or a package whose checksum no longer matches what was
  validated, is refused in words a person can act on.
- **It is not done until it is read back.** The deployment's `checks` — every chart arrived, the audience is
  the one SharpSight decided, and *this chart returns data* for each chart — are the answer to « everything
  works without any issue », and the Studio shows them under the steps.
- **A target is named in the engine's environment and nowhere in this repository.** Five variables
  (`AIBI_TARGET_<NAME>_URL`, `_USER`, `_PASSWORD`, `_DB_NAME`, `_DB_URI`), plus `_FROM_STUDIO=false` to make a
  target the command line's alone and `_PUBLIC_URL` where a person reaches it by another address than the
  engine does. Nothing is deployed anywhere that is not named there.

For trying it on one machine, `docker-compose.run-target.yaml` starts a **second Superset** — its own metadata
database, its own accounts, its own URL, and nothing of the kit's in it: no SharpSight menu, no Studio, no
seeded connection. The deployment has to bring everything it needs, which is the point.

## Consequences

- A dashboard in a run environment is traceable to a version, a validation and two people, because that is
  all a deployment can carry.
- The deployment is only as good as the package. A version committed before packages existed cannot be
  deployed, and says so.
- Reading every chart back costs one query per chart against the target's warehouse. That is accepted: it is
  the only way an import becomes a dashboard somebody can be told to open.
- A target's own Superset version is only known when it is called, so it is not in the matrix's `components`
  (which are read live out of this machine) but in its own `deployment_targets` section. The deployment reads
  it at step two and refuses to carry a package across a version it was not built against — before anything
  is imported. Deciding that a newer Superset is safe to deploy into is a matrix decision, taken there and not
  in the deployer.
- The Studio can deploy (the button is in the Deployments tab) and cannot approve: the same allowlist as
  everything else it asks of the engine ([ADR 0011](0011-the-gateway-token-has-an-allowlist.md)).
