# ADR 0013 — The Studio shows no sample of the data

*Status: accepted.*

## Context

The brief's drawer (§47.7) lists a « limited, permission-checked sample ». The engine can do it: the data
gateway is read-only, row-level rules are applied, and columns a person may not read are withheld.

But this kit deliberately gives a developer's Superset account **no database in SQL Lab**: their account opens
the page the panel is on and can query nothing. Whether that should change is one of the owner's open
questions.

## Decision

The Studio shows no sample of the data. Its « Data & dbt » tab shows what is governed about the dataset — the
owner, the domain, the grain, the dbt model it is built from, what that model is built from — and says plainly
why there is no sample.

## Consequences

- The Studio is not a way round a decision taken elsewhere about the same people.
- « Why is this chart empty » cannot be answered from the page; it is answered by a commit's own validation,
  which reads the data as the dashboard does, and by Preview.
- If the owner decides a developer may query, this is one tab and one route — and it should then be the same
  permission, not a second one.
