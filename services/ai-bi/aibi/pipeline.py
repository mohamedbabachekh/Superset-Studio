"""A request, carried through its twenty-two steps to a version awaiting review — or stopped, saying where.

The steps are the brief's (section 9), in its order, and a run records each one: when it started, what it
found, why it was skipped. A step that fails stops the run there. Nothing after a failure is attempted, and
a version is only ever written by a run that reached the end with a validation that passed — there is no
« generated but not quite valid » state for a reviewer to find.

Everything that touches data, dbt, git or the BI engine goes through the tool layer. The decisions between
the tools are the agents'.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Optional

from .audit import AuditLog
from .dashboard_agent import Draft
from .dataset_agent import Candidate, DatasetAgent, Gap
from .lifecycle import Lifecycle
from .llm import RequirementParser
from .models import ModelPlan, RunRecord, StepRecord, ValidationReport, utcnow
from .planner import slugify
from .policy_engine import Identity, PolicyError
from .store import Store
from .tools import Toolbox, ToolContext


class PipelineError(Exception):
    pass


class _Steps:
    def __init__(self, run: RunRecord, store: Store):
        self.run = run
        self.store = store
        self._by_key = {s.key: s for s in run.steps}

    @contextmanager
    def step(self, key: str) -> Iterator[StepRecord]:
        record = self._by_key[key]
        record.status, record.started_at = "running", utcnow()
        self.store.save_run(self.run)
        try:
            yield record
        except Exception as exc:
            record.status, record.error, record.ended_at = "failed", str(exc)[:1500], utcnow()
            self.store.save_run(self.run)
            raise
        record.status, record.ended_at = "done", utcnow()
        self.store.save_run(self.run)

    def skip(self, key: str, reason: str) -> None:
        record = self._by_key[key]
        record.status, record.summary = "skipped", {"reason": reason}
        self.store.save_run(self.run)


def _dbt_ok(outcome: dict[str, Any], what: str) -> dict[str, Any]:
    if not outcome["ok"]:
        failed = "; ".join(f"{f['unique_id']}: {f['message']}" for f in outcome["failures"]) or outcome["output"][-600:]
        raise PipelineError(f"{what} failed — {failed}")
    return {k: outcome[k] for k in ("command", "duration_ms", "statuses", "nodes")}


class Pipeline:
    def __init__(self, tools: Toolbox, lifecycle: Lifecycle, parser: RequirementParser):
        self.tools = tools
        self.lifecycle = lifecycle
        self.parser = parser
        self.registry = tools.registry
        self.store: Store = tools.store
        self.audit: AuditLog = tools.audit
        self.datasets = DatasetAgent(tools.registry, tools.semantic)

    def execute(self, run: RunRecord, identity: Identity) -> RunRecord:
        ctx = ToolContext(identity=identity, request_id=run.request_id, ai_model=run.ai_model)
        self.audit.record(action="request", user=identity.user, request_id=run.request_id, actor="human",
                          ai_model=run.ai_model, detail={"prompt": run.prompt, "run_id": run.id})
        notes: list[str] = []
        try:
            self.tools.policy.require(identity, "bi.generate")
            self._run(run, ctx, _Steps(run, self.store), notes)
            run.status = "succeeded"
        except Exception as exc:  # noqa: BLE001 — whatever stopped it, the run must say so
            run.status, run.error = "failed", f"{type(exc).__name__}: {str(exc)[:1500]}"
            aftermath = self._put_back(run, ctx)
            if aftermath:
                run.error += f" — {aftermath}"
            self.audit.record(action="run:failed", user=identity.user, request_id=run.request_id, actor="ai",
                              ai_model=run.ai_model, project_id=run.project_id,
                              outcome="refused" if isinstance(exc, PolicyError) else "error",
                              detail={"error": run.error, "run_id": run.id})
        run.ended_at = utcnow()
        self.store.save_run(run)
        return run

    def _put_back(self, run: RunRecord, ctx: ToolContext) -> Optional[str]:
        """A run that failed AFTER it changed the BI engine must not leave a reviewed dashboard altered: the
        latest version's specification is re-applied from git. Returns what to tell the requester."""
        touched = next(s for s in run.steps if s.key == "create_superset_assets").status != "pending"
        if not touched or not ctx.project_id:
            return None
        latest = self.store.latest_version(ctx.project_id)
        if latest is None:
            return "the dashboard in the BI engine holds this failed attempt, as an unpublished draft"
        try:
            spec = self.lifecycle.specification_at(ctx.project_id, latest.version)
            if not self.tools.save_dashboard_spec(ctx, spec=spec).passed:
                raise PipelineError("its specification is no longer valid")
            self.tools.apply_specification(ctx)
            drift = self.lifecycle.drift(latest)
        except Exception as exc:  # noqa: BLE001
            return (f"the dashboard in the BI engine holds this failed attempt and could NOT be put back as "
                    f"version {latest.version}: {str(exc)[:300]}")
        self.audit.record(action="restore", user=ctx.identity.user, request_id=ctx.request_id, actor="ai",
                          ai_model=ctx.ai_model, project_id=ctx.project_id,
                          detail={"restored_version": latest.version, "reason": "the run failed", "drift": drift})
        return (f"the dashboard was put back as version {latest.version}" +
                ("" if drift is None else f", but {drift}"))

    # ── the twenty-two steps ────────────────────────────────────────────────────────────────────────────────
    def _run(self, run: RunRecord, ctx: ToolContext, steps: _Steps, notes: list[str]) -> None:
        tools, registry = self.tools, self.registry
        identity = ctx.identity

        with steps.step("parse_requirement") as step:
            requirement = self.parser.parse(run.prompt)
            notes.extend(requirement.unresolved)
            if requirement.template is None:
                notes.append("no registered template was named: the executive template is used")
            template = registry.template(requirement.template or "executive")
            project_id = slugify(requirement.subject)
            run.project_id = ctx.project_id = project_id
            project = self.store.get_project(project_id) or {
                "project_id": project_id, "tenant": identity.tenant, "created_by": identity.user,
                "created_at": utcnow()}
            if project["tenant"] != identity.tenant:
                raise PolicyError(f"{identity.user} has no access to BI project {project_id}", "tenant")
            latest = self.store.latest_version(project_id)
            if latest and latest.state in ("UAT", "PROD") and latest.review_status != "REJECTED":
                way_out = ("ask under another subject, or have it rejected in review first" if latest.state == "UAT"
                           else "ask under another subject")
                raise PipelineError(
                    f"BI project {project_id} v{latest.version} is in {latest.state}. This kit has ONE BI engine, "
                    f"so regenerating it would rewrite the promoted dashboard in place; {way_out}.")
            project.update(title=requirement.subject, template=template.id, spec_valid=False)
            tools.save_project(project)
            step.summary = {"project_id": project_id, "subject": requirement.subject, "template": template.id,
                            "metrics": requirement.metrics, "views": [v.model_dump() for v in requirement.views],
                            "filters": requirement.filters, "not_understood": requirement.unresolved,
                            "read_by": requirement.parser}
            if not requirement.metrics:
                raise PipelineError("the request names no registered metric, and the engine does not invent one")

        with steps.step("find_datasets") as step:
            permitted = [registry.dataset(d["id"]) for d in tools.list_datasets(ctx)]
            wanted = [registry.metrics[m] for m in requirement.metrics if m in registry.metrics]
            candidates = self.datasets.discover(requirement, permitted, wanted, project_id)
            if not candidates:
                raise PipelineError(f"no dataset {identity.user} may read matches « {requirement.subject} »")
            for candidate in candidates[:3]:
                tools.get_dataset_schema(ctx, dataset_id=candidate.dataset.id)
            step.summary = {"readable_by_requester": len(permitted),
                            "candidates": [c.describe() for c in candidates]}

        with steps.step("find_metrics") as step:
            metrics, metric_notes = tools.semantic.find_metrics(requirement)
            notes.extend(metric_notes)
            for metric in metrics:
                tools.get_metric(ctx, metric_id=metric.id)
            step.summary = {"metrics": [{"id": m.id, "certified": m.certified, "version": m.version,
                                         "owner": m.owner} for m in metrics], "notes": metric_notes}
            if not metrics:
                raise PipelineError("none of the requested metrics is registered")

        with steps.step("identify_missing_data") as step:
            drafts, draft_notes = tools.dashboards.draft(requirement, template, metrics)
            notes.extend(draft_notes)
            gaps: list[tuple[Draft, Gap]] = []
            for draft in list(drafts):
                if not self._resolve(draft, candidates, gaps):
                    drafts.remove(draft)
                    notes.append(f"« {draft.phrase or draft.slot} » left out: {draft.metric.name} is not valid on "
                                 "any dataset the requester may read")
            step.summary = {"components": [{"slot": d.slot, "type": d.type, "metric": d.metric.id,
                                            "dataset": d.dataset} for d in drafts],
                            "missing": [gap.describe() | {"slot": draft.slot} for draft, gap in gaps]}

        with steps.step("determine_transformations") as step:
            closable: list[tuple[Draft, Gap]] = []
            providers = []
            for draft, gap in gaps:
                provider = self.datasets.provider_for(gap, permitted)
                if provider is not None and (not closable or gap.base.id == closable[0][1].base.id):
                    closable.append((draft, gap))
                    providers.append(provider)
                    continue
                alternative = tools.dashboards.fallback(draft, template)
                if alternative is not None and self._resolve(alternative, candidates, []) and alternative.dataset:
                    drafts[drafts.index(draft)] = alternative
                    notes.append(f"« {draft.phrase or draft.slot} » is drawn as a {alternative.type}: {gap.reason}")
                else:
                    drafts.remove(draft)
                    notes.append(f"« {draft.phrase or draft.slot} » left out: {gap.reason}, and no governed "
                                 "dimension can supply it")
            plan: Optional[ModelPlan] = None
            reason = "every component is served by an existing dataset"
            if closable:
                plan = tools.dbt_agent.plan(project_id, [gap for _, gap in closable], providers)
                reason = plan.reason
            else:
                # a model this project generated before is rebuilt, so that what is validated is today's data
                mine = [d.dataset for d in drafts if d.dataset and registry.dataset(d.dataset).project == project_id]
                if mine and project.get("model_plan"):
                    plan = ModelPlan(**project["model_plan"])
                    reason = f"{mine[0]} was generated for this project earlier; it is rebuilt"
            step.summary = {"transformation_needed": plan is not None, "reason": reason,
                            "plan": plan.model_dump() if plan else None}

        dbt_results: list[dict[str, Any]] = []
        if plan is None:
            for key in ("generate_dbt_model", "dbt_compile", "dbt_test", "dbt_run", "register_dataset"):
                steps.skip(key, "no transformation is needed")
        else:
            with steps.step("generate_dbt_model") as step:
                generated = tools.generate_dbt_model(ctx, plan=plan)
                project = tools.project(ctx)
                step.summary = {"model": plan.name, "files": generated["files"],
                                "branch": project["dbt"]["branch"], "commit": generated["commit"],
                                "target_schema": tools.dbt_dev_schema}
            with steps.step("dbt_compile") as step:
                outcome = tools.compile_dbt(ctx, select=plan.name)
                step.summary = _dbt_ok(outcome, "dbt compile")
            with steps.step("dbt_test") as step:
                # what the model is built FROM is tested before it is built
                outcome = tools.run_dbt_test(ctx, select=f"1+{plan.name}", exclude=plan.name)
                dbt_results.append({"stage": "upstream tests", **outcome})
                step.summary = _dbt_ok(outcome, "the tests of the model's sources")
            with steps.step("dbt_run") as step:
                outcome = tools.run_dbt_model(ctx, select=plan.name)
                dbt_results.append({"stage": "run", **outcome})
                built = _dbt_ok(outcome, "dbt run")
                outcome = tools.run_dbt_test(ctx, select=plan.name)
                dbt_results.append({"stage": "model tests", **outcome})
                step.summary = {"run": built, "tests": _dbt_ok(outcome, "the generated model's own tests")}
            with steps.step("register_dataset") as step:
                node = self.store.lineage_for(plan.name) or {}
                dataset = tools.dbt_agent.dataset_for(plan, project_id, tools.dbt_dev_schema, node.get("columns") or None)
                path = registry.register_generated(dataset)
                rows = tools.execute_readonly_sql(ctx, sql=tools.sql.row_count(dataset),
                                                  purpose=f"inspect the built {dataset.id}").scalar()
                candidates.append(Candidate(dataset, 0.0, ["generated for this project"]))
                added = [c for j in plan.joins for c in j.add_columns]
                for draft, gap in closable:
                    draft.dataset = dataset.id
                    draft.dimension = gap.need.column or dataset.columns_with_role(gap.need.role)[0]
                step.summary = {"dataset": dataset.id, "certified": False, "relation": dataset.relation,
                                "rows": rows, "added_columns": added, "registered_in": path.name,
                                "lineage": [p["name"] for p in node.get("depends_on", [])],
                                "tests": node.get("tests", [])}

        with steps.step("generate_specification") as step:
            spec, spec_notes = tools.dashboards.specify(project_id, requirement, template, drafts)
            notes.extend(spec_notes)
            step.summary = {"specification": spec.model_dump(exclude_none=True), "notes": spec_notes}

        with steps.step("validate_specification") as step:
            verdict = tools.save_dashboard_spec(ctx, spec=spec)
            step.summary = verdict.summary()
            if not verdict.passed:
                raise PipelineError("the specification is not valid, so the BI engine was not called: "
                                    + "; ".join(f"{c.name} ({c.subject}) {c.detail}".strip() for c in verdict.failures))

        with steps.step("create_superset_assets") as step:
            applied = tools.apply_specification(ctx)
            step.summary = {"dashboard": applied["dashboard"], "datasets": applied["datasets"],
                            "charts": {slot: {"id": c["id"], "name": c["name"], "engine_type": c["engine_type"]}
                                       for slot, c in applied["charts"].items()},
                            "filters": applied["filters"], "template": f"{template.id}@{template.version}"}

        with steps.step("retrieve_dashboard_data") as step:
            retrieved = tools.get_dashboard_data(ctx)
            step.summary = {"components": {slot: {"rows": o["rowcount"], "ms": o["duration_ms"], "error": o["error"]}
                                           for slot, o in retrieved["components"].items()},
                            "filter_probes": len(retrieved["filter_probes"])}

        with steps.step("validate_query_results") as step:
            report = tools.validate_dashboard(ctx, visual=False)
            step.summary = self._verdict(report)

        with steps.step("render_screenshot") as step:
            renders = tools.render_dashboard_screenshot(ctx)
            step.summary = {"renders": [r.describe() for r in renders]}

        with steps.step("visual_qa") as step:
            report = tools.validate_dashboard(ctx, visual=True)
            step.summary = self._verdict(report)

        if report.passed:
            steps.skip("fix_problems", "validation found nothing to fix")
            steps.skip("re_render", "nothing was changed after the first render")
        else:
            short_names = {m.id: m.short_name for m in metrics if m.short_name}
            limit = int(registry.policies.get("visual", {}).get("max_fix_iterations", 3))
            applied_fixes: list[dict[str, Any]] = []
            with steps.step("fix_problems") as step:
                iteration = 0
                while not report.passed and iteration < limit:
                    fixes = tools.visual.propose_fixes(report, spec, template, short_names)
                    if not fixes:
                        break
                    iteration += 1
                    for fix in fixes:
                        fix.apply(spec)
                        applied_fixes.append({"iteration": iteration, "slot": fix.slot, "problem": fix.code,
                                              "fix": fix.description})
                    verdict = tools.save_dashboard_spec(ctx, spec=spec)
                    if not verdict.passed:
                        raise PipelineError("a fix produced a specification that is not valid: "
                                            + "; ".join(c.detail for c in verdict.failures))
                    tools.apply_specification(ctx)
                    tools.get_dashboard_data(ctx)
                    renders = tools.render_dashboard_screenshot(ctx)
                    report = tools.validate_dashboard(ctx, visual=True)
                step.summary = {"iterations": iteration, "fixes": applied_fixes,
                                "left_unfixed": [f"{c.category} · {c.name} · {c.subject}: {c.detail}"
                                                 for c in report.failures]}
                notes.extend(f"fixed automatically — {f['slot']}: {f['fix']}" for f in applied_fixes)
            with steps.step("re_render") as step:
                step.summary = {"renders": [r.describe() for r in renders], **self._verdict(report)}
                if not report.passed:
                    raise PipelineError(
                        f"validation still fails after {len(applied_fixes)} automatic fix(es): "
                        + "; ".join(f"{c.category} · {c.name} · {c.subject}: {c.detail}" for c in report.failures[:6]))

        with steps.step("store_version") as step:
            record = self.lifecycle.store_version(ctx, requirement.model_dump(), report, dbt_results, notes)
            step.summary = {"version": record.version, "superset_asset_version": record.superset_asset_version,
                            "dataset_versions": record.dataset_versions, "metric_versions": record.metric_versions,
                            "template_version": record.template_version, "notes": notes}

        with steps.step("git_commit") as step:
            record = self.lifecycle.commit_version(ctx, record)
            step.summary = {"git_commit": record.git_commit, "dbt_commit": record.dbt_commit,
                            "tag": f"{project_id}/v{record.version}", "repository": "bi-projects"}

        with steps.step("mark_ready_for_review") as step:
            record = self.lifecycle.mark_ready(ctx, record)
            run.version = record.version
            step.summary = {"project_id": project_id, "version": record.version, "state": record.state,
                            "review_status": record.review_status, "dashboard": record.dashboard.get("url")}

    def _resolve(self, draft: Draft, candidates: list[Candidate], gaps: list[tuple[Draft, Gap]]) -> bool:
        """Give the draft its dataset, or record what is missing. False when the metric is valid nowhere."""
        dataset, gap = self.datasets.resolve(draft.need(), candidates)
        if dataset is not None:
            draft.dataset = dataset.id
            draft.dimension = draft.column or (dataset.columns_with_role(draft.role)[0] if draft.role else None)
            return True
        if gap is not None:
            gaps.append((draft, gap))
            return True
        return False

    @staticmethod
    def _verdict(report: ValidationReport) -> dict[str, Any]:
        return {**report.summary(),
                "failed": [{"category": c.category, "check": c.name, "subject": c.subject, "detail": c.detail}
                           for c in report.failures],
                "warned": [{"category": c.category, "check": c.name, "subject": c.subject, "detail": c.detail}
                           for c in report.warnings]}
