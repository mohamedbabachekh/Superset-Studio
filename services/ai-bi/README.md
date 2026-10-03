# services/ai-bi

The SharpSight AI BI service: a request in, a governed, validated, versioned dashboard out.
**How to run it, what it does and what it does not do yet is in [docs/ai-bi.md](../../docs/ai-bi.md).**

| | |
|---|---|
| `aibi/main.py` | the API the portal calls |
| `aibi/pipeline.py` | the twenty-two steps of a generation, and the quality gates beside them |
| `aibi/planner.py` · `aibi/llm.py` | reading a request; where a language model plugs in |
| `aibi/tools.py` | the tool layer — the only way the engine acts |
| `aibi/policy_engine.py` · `aibi/data_gateway.py` | who may do what; the one read-only road to the data |
| `aibi/dataset_agent.py` · `aibi/semantic_agent.py` · `aibi/sql_agent.py` | discovery, governed metrics, the SQL it writes |
| `aibi/dbt_agent.py` · `aibi/dbt_runner.py` · `aibi/dbt_build.py` | a transformation as a plan; dbt in a workspace; the production build |
| `aibi/dashboard_agent.py` · `aibi/chart_agent.py` | the specification; one component → one chart |
| `aibi/superset_adapter.py` · `aibi/adapter_check.py` | Superset's REST API, and nothing but; every road of it driven on a copy (`bash ai-bi.sh check-adapter`) |
| `aibi/validation_agent.py` · `aibi/visual_qa_agent.py` | the validation engine; looking at the result |
| `aibi/lifecycle.py` · `aibi/git_adapter.py` | versions, the `.ssbi` package, the gates from review to PROD, restore |
| `aibi/compatibility.py` | the compatibility matrix, beside what is actually running |
| `aibi/assets.py` · `aibi/library.py` | the asset library: one catalogue over everything, where each asset is used and what a change would touch; the shared charts and sections |
| `aibi/studio/` | BI Studio: static files the service serves at `/studio/` — a frame around Superset (the live dashboard, Explore, SQL Lab) and around dbt's own documentation, with SharpSight's library, structure, governance and gates beside them |
| `aibi/audit.py` · `aibi/store.py` | the audit trail; SharpSight's own memory of projects |
| `sql/bootstrap.sql` | the warehouse roles, schemas and the landed extract |
| `tests/` | the rules, held to account without a database (`bash ai-bi.sh test`) |
