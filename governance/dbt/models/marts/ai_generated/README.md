# Models the AI generates

Nothing is committed here in the governed project. The AI BI service writes into this folder **only on its own
branch** (`ai/<project>`) of its own clone, builds it with the `dev` target into the schema `bi_dev`, and the
files travel to review inside the project's `.ssbi` package. A model arrives in this repository the way any
model does: through a human merging it.

What a generated model may contain is held to `governance/registry/policies.yaml` (`dbt.forbidden_patterns`): a single
`select` over `ref()`s of datasets the requesting user may read — no hooks, no `run_query`, no macros that
reach the adapter.
