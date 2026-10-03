"""What may be done, by whom, to what. Every tool passes through here before it touches anything.

Three questions are answered in this file and nowhere else:
  · who is asking, and what are they allowed (RBAC, tenant, classification);
  · may this SQL run (one statement, a query, over registered datasets the asker may read, nothing that
    writes, waits, or reaches outside);
  · may this generated dbt model be built (the same test, plus no way to run SQL outside its own select).

The database enforces the same things a second time — a read-only role, a statement timeout, a schema the AI
cannot create in. Neither layer trusts the other to be enough.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Optional

import sqlglot
from sqlglot import exp

from .models import RESTRICTED_CLASSIFICATIONS, DatasetDef, MetricDef
from .registry import Registry


class PolicyError(Exception):
    def __init__(self, message: str, code: str = "denied"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Identity:
    user: str
    type: str
    tenant: str
    roles: tuple[str, ...]
    capabilities: frozenset[str]
    dataset_patterns: tuple[str, ...]
    classifications: frozenset[str]


@dataclass
class SqlVerdict:
    ok: bool
    problems: list[str] = field(default_factory=list)
    datasets: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)


# Statement kinds that are never a query. Looked up by name so that a sqlglot without one of them still loads.
_WRITE_NODES = tuple(node for node in (getattr(exp, name, None) for name in (
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable", "TruncateTable", "Copy",
    "Command", "Set", "Transaction", "Commit", "Rollback", "Grant", "Revoke", "Use", "Analyze", "Into", "Lock",
    "LoadData", "Refresh", "Comment", "Kill", "Pragma",
)) if node is not None)

_APPROVAL_CAPABILITY = {"UAT": "bi.approve_uat", "PROD": "bi.approve_prod"}


def _combine(predicates: list[str], operator: str) -> Optional[str]:
    unique = list(dict.fromkeys(predicates))
    if not unique:
        return None
    return unique[0] if len(unique) == 1 else f" {operator} ".join(f"({p})" for p in unique)


class PolicyEngine:
    def __init__(self, registry: Registry):
        self.registry = registry

    # ── identity ────────────────────────────────────────────────────────────────────────────────────────────
    def identify(self, user: str) -> Identity:
        body = self.registry.rbac.users.get(user)
        if body is None:
            raise PolicyError(f"{user} is not a known SharpSight user", "unknown_user")
        capabilities: set[str] = set()
        patterns: list[str] = []
        classifications: set[str] = set()
        for role_name in body.roles:
            role = self.registry.rbac.roles[role_name]
            capabilities.update(role.capabilities)
            patterns.extend(role.datasets)
            classifications.update(role.classifications)
        return Identity(user=user, type=body.type, tenant=body.tenant, roles=tuple(body.roles),
                        capabilities=frozenset(capabilities), dataset_patterns=tuple(patterns),
                        classifications=frozenset(classifications))

    def require(self, identity: Identity, capability: str) -> None:
        if capability not in identity.capabilities:
            raise PolicyError(f"{identity.user} may not do this (it needs {capability})", "missing_capability")

    # ── datasets ────────────────────────────────────────────────────────────────────────────────────────────
    def can_read(self, identity: Identity, dataset: DatasetDef) -> bool:
        if dataset.tenant != identity.tenant:
            return False
        return any(fnmatch.fnmatchcase(dataset.id, pattern) for pattern in identity.dataset_patterns)

    def identity_for_role(self, role_name: str, tenant: str) -> Identity:
        """A stand-in holding exactly one role — what a probe account in the BI engine is compared against."""
        role = self.registry.rbac.roles[role_name]
        return Identity(user=f"role:{role_name}", type="service", tenant=tenant, roles=(role_name,),
                        capabilities=frozenset(role.capabilities), dataset_patterns=tuple(role.datasets),
                        classifications=frozenset(role.classifications))

    def require_dataset(self, identity: Identity, dataset_id: str) -> DatasetDef:
        dataset = self.registry.dataset(dataset_id)
        if not self.can_read(identity, dataset):
            # the same answer whether it is another tenant's or simply not granted: say nothing about which
            raise PolicyError(f"{identity.user} has no access to dataset {dataset_id}", "dataset_denied")
        return dataset

    def permitted_datasets(self, identity: Identity) -> list[DatasetDef]:
        return [d for d in self.registry.datasets.values() if self.can_read(identity, d)]

    def readable_columns(self, identity: Identity, dataset: DatasetDef) -> list[str]:
        return [name for name, col in dataset.columns.items() if col.classification in identity.classifications]

    def withheld_columns(self, identity: Identity, dataset: DatasetDef) -> list[str]:
        return [name for name, col in dataset.columns.items() if col.classification not in identity.classifications]

    # ── metrics ─────────────────────────────────────────────────────────────────────────────────────────────
    def cleared_for(self, identity: Identity, metric: MetricDef) -> bool:
        """A metric has a security classification of its own; a requester sees it only when cleared for it."""
        return metric.classification in identity.classifications

    def may_use(self, identity: Identity, metric: MetricDef) -> bool:
        """Cleared for the metric, and granted at least one dataset it is computed on."""
        return self.cleared_for(identity, metric) and any(
            self.can_read(identity, self.registry.datasets[ds]) for ds in metric.lineage.datasets
            if ds in self.registry.datasets)

    def require_metric(self, identity: Identity, metric_id: str) -> MetricDef:
        metric = self.registry.metric(metric_id)
        if not self.may_use(identity, metric):
            # one answer for « not granted » and « not cleared »: say nothing about which
            raise PolicyError(f"{identity.user} has no access to metric {metric_id}", "metric_denied")
        return metric

    # ── SQL ─────────────────────────────────────────────────────────────────────────────────────────────────
    def validate_sql(self, sql: str, identity: Identity) -> SqlVerdict:
        verdict = SqlVerdict(ok=False, checks={"syntax": False, "read_only": False, "allowed_database": False,
                                               "forbidden_operations": False, "authorized_datasets": False})
        try:
            statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
        except sqlglot.errors.SqlglotError as exc:
            verdict.problems.append(f"syntax: {str(exc).splitlines()[0]}")
            return verdict
        if len(statements) != 1:
            verdict.problems.append(f"exactly one statement is allowed, {len(statements)} were given")
            return verdict
        tree = statements[0]
        verdict.checks["syntax"] = True

        # read-only: the statement is a query, and nothing inside it writes, locks or selects INTO
        if not isinstance(tree, exp.Query):
            verdict.problems.append(f"only a SELECT may run here, this is {tree.key.upper()}")
        writers = sorted({node.key.upper() for node in tree.find_all(*_WRITE_NODES)}) if _WRITE_NODES else []
        if writers:
            verdict.problems.append("it contains " + ", ".join(writers) + ", which is not a read")
        verdict.checks["read_only"] = isinstance(tree, exp.Query) and not writers

        # forbidden operations: functions that wait, read files, or reach another server
        forbidden = {f.lower() for f in self.registry.policies.get("sql", {}).get("forbidden_functions", [])}
        used: set[str] = set()
        for func in tree.find_all(exp.Func):
            name = (func.name if isinstance(func, exp.Anonymous) else func.sql_name()).lower()
            if name in forbidden:
                used.add(name)
        if used:
            verdict.problems.append("forbidden function: " + ", ".join(sorted(used)))
        verdict.checks["forbidden_operations"] = not used

        # allowed database, and authorised datasets: every table is a registered dataset the asker may read
        allowed_schemas = set(self.registry.policies.get("sql", {}).get("allowed_schemas", []))
        cte_names = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
        database_ok = datasets_ok = True
        touched: list[DatasetDef] = []
        for table in tree.find_all(exp.Table):
            if not table.name:
                continue
            schema = table.db or None
            if schema is None and table.name in cte_names:
                continue
            label = f"{schema}.{table.name}" if schema else table.name
            if label not in verdict.tables:
                verdict.tables.append(label)
            if table.catalog:
                database_ok = False
                verdict.problems.append(f"{table.catalog}.{label}: naming another database is not allowed")
                continue
            if schema is not None and schema not in allowed_schemas:
                database_ok = False
                verdict.problems.append(f"{label}: schema {schema} is not one a query may read")
                continue
            dataset = self.registry.dataset_by_relation(schema, table.name)
            if dataset is None:
                datasets_ok = False
                verdict.problems.append(f"{label} is not a registered dataset")
                continue
            if not self.can_read(identity, dataset):
                datasets_ok = False
                verdict.problems.append(f"{identity.user} has no access to dataset {dataset.id}")
                continue
            if dataset not in touched:
                touched.append(dataset)
        verdict.datasets = [d.id for d in touched]

        # classification: a column the asker may not read cannot be named, and `*` cannot fetch it either
        column_names = {c.name for c in tree.find_all(exp.Column)}
        has_star = any(not isinstance(star.parent, exp.Count) for star in tree.find_all(exp.Star))
        for dataset in touched:
            withheld = set(self.withheld_columns(identity, dataset))
            if not withheld:
                continue
            named = sorted(withheld & column_names)
            if named:
                datasets_ok = False
                verdict.problems.append(f"{dataset.id}: column(s) {', '.join(named)} are classified above "
                                        f"what {identity.user} may read")
            elif has_star:
                datasets_ok = False
                verdict.problems.append(f"{dataset.id}: `*` would return classified columns; name the columns")
        verdict.checks["allowed_database"] = database_ok
        verdict.checks["authorized_datasets"] = datasets_ok
        verdict.ok = not verdict.problems
        return verdict

    def secure_sql(self, sql: str, identity: Identity, limit: int) -> str:
        """The statement that actually runs: row-level policies applied, and a hard row cap around it."""
        tree = sqlglot.parse_one(sql, read="postgres")
        cte_names = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}

        def restrict(node: exp.Expression) -> exp.Expression:
            if not isinstance(node, exp.Table) or (not node.db and node.name in cte_names):
                return node
            dataset = self.registry.dataset_by_relation(node.db or None, node.name)
            predicate = self.row_policy(identity, dataset) if dataset else None
            if predicate is None:
                return node
            inner = exp.select("*").from_(exp.table_(node.name, db=node.db or None)).where(predicate)
            return inner.subquery(alias=node.alias_or_name)

        body = tree.transform(restrict).sql(dialect="postgres")
        return f"select * from (\n{body}\n) as governed_query limit {int(limit)}"

    # ── row-level policies ──────────────────────────────────────────────────────────────────────────────────
    def grants(self, role_name: str, dataset: DatasetDef) -> bool:
        role = self.registry.rbac.roles[role_name]
        return any(fnmatch.fnmatchcase(dataset.id, pattern) for pattern in role.datasets)

    def role_row_policy(self, role_name: str, dataset: DatasetDef) -> Optional[str]:
        """One role's predicate on a dataset: its own, AND the one it carries on every dataset this one is
        derived from. A model that joins a column onto a mart must not be a way around the mart's policy —
        and when the derived dataset no longer has the columns the policy speaks of, the role sees nothing
        of it rather than everything."""
        declared = self.registry.rbac.row_filters.get(role_name, {})
        predicates = [declared[dataset.id]] if dataset.id in declared else []
        for parent_id in dataset.derived_from:
            parent = self.registry.datasets.get(parent_id)
            inherited = self.role_row_policy(role_name, parent) if parent is not None else None
            if inherited is None:
                continue
            columns = {c.name for c in sqlglot.parse_one(inherited, read="postgres").find_all(exp.Column)}
            predicates.append(inherited if columns <= set(dataset.columns) else "1 = 0")
        return _combine(predicates, "and")

    def row_policy(self, identity: Identity, dataset: DatasetDef) -> Optional[str]:
        """What restricts this identity on this dataset: the policies of its roles that grant the dataset,
        several seeing the union. A role WITHOUT a policy does not lift another role's — which is the BI
        engine's own rule for row-level security, and the two layers must give the same rows."""
        return _combine([p for p in (self.role_row_policy(role, dataset)
                                     for role in identity.roles if self.grants(role, dataset)) if p], "or")

    def row_policies(self, identity: Identity, dataset_id: str) -> Optional[str]:
        return self.row_policy(identity, self.registry.dataset(dataset_id))

    # ── who a dashboard is for ──────────────────────────────────────────────────────────────────────────────
    def audience(self, datasets: list[DatasetDef], metrics: Optional[list[MetricDef]] = None) -> list[str]:
        """The roles a dashboard over these datasets may be opened by: those that may read, are granted
        EVERY one of its datasets, and are cleared for EVERY one of its metrics. A role granted three of
        four would see a dashboard with a hole in it; a role not cleared for a metric would be shown it."""
        return sorted(name for name, role in self.registry.rbac.roles.items()
                      if "bi.read" in role.capabilities and datasets and all(self.grants(name, d) for d in datasets)
                      and all(m.classification in role.classifications for m in metrics or []))

    def row_rules(self, datasets: list[DatasetDef], metrics: Optional[list[MetricDef]] = None) -> list[dict[str, str]]:
        """Every (role, dataset, predicate) the BI engine must enforce for that audience."""
        return [{"role": role, "dataset": dataset.id, "clause": clause}
                for role in self.audience(datasets, metrics) for dataset in datasets
                if (clause := self.role_row_policy(role, dataset)) is not None]

    # ── generated dbt models ────────────────────────────────────────────────────────────────────────────────
    _REF = re.compile(r"\{\{\s*ref\(\s*['\"]([A-Za-z0-9_]+)['\"]\s*\)\s*\}\}")
    _CONFIG = re.compile(r"\{\{\s*config\((.*?)\)\s*\}\}", re.S)

    def validate_dbt_model(self, text: str, identity: Identity) -> SqlVerdict:
        """A generated model is a config line and one select over ref()s — nothing that runs on its own."""
        verdict = SqlVerdict(ok=False)
        policy = self.registry.policies.get("dbt", {})
        lowered = text.lower()
        for pattern in policy.get("forbidden_patterns", []):
            if pattern.lower() in lowered:
                verdict.problems.append(f"a generated model may not contain `{pattern}`")
        body = text
        for config in self._CONFIG.findall(text):
            keys = set(re.findall(r"(\w+)\s*=", config))
            extra = keys - {"materialized", "tags"}
            if extra:
                verdict.problems.append("config() may only set materialized and tags, not " + ", ".join(sorted(extra)))
            wanted = re.search(r"materialized\s*=\s*['\"](\w+)['\"]", config)
            if wanted and wanted.group(1) not in policy.get("generated_materializations", ["table"]):
                verdict.problems.append(f"materialized='{wanted.group(1)}' is not allowed for a generated model")
        body = self._CONFIG.sub("", body)
        body = "\n".join(line for line in body.splitlines() if not line.strip().startswith("--"))

        refs = self._REF.findall(body)
        for model in dict.fromkeys(refs):
            dataset = self.registry.dataset_by_relation(None, model)
            if dataset is None:
                verdict.problems.append(f"ref('{model}') is not the model of a registered dataset")
        rendered = self._REF.sub(lambda m: self._relation_for(m.group(1)), body)
        if "{{" in rendered or "{%" in rendered:
            verdict.problems.append("a generated model may use ref() and config() only, no other Jinja")
        if verdict.problems:
            return verdict
        inner = self.validate_sql(rendered, identity)
        inner.ok = not inner.problems
        return inner

    def _relation_for(self, model: str) -> str:
        dataset = self.registry.dataset_by_relation(None, model)
        return dataset.relation if dataset else model

    # ── approvals ───────────────────────────────────────────────────────────────────────────────────────────
    def require_approver(self, identity: Identity, target: str, created_by: str) -> None:
        if identity.type != "human":
            raise PolicyError("only a person can approve a promotion", "not_human")
        self.require(identity, _APPROVAL_CAPABILITY[target])
        four_eyes = self.registry.policies.get("deployment", {}).get("four_eyes_for_prod", True)
        if target == "PROD" and four_eyes and identity.user == created_by:
            raise PolicyError("production needs a second person: the requester cannot approve their own "
                              "dashboard", "four_eyes")

    # ── theme ───────────────────────────────────────────────────────────────────────────────────────────────
    def restricted(self, dataset: DatasetDef) -> list[str]:
        return [name for name, col in dataset.columns.items() if col.classification in RESTRICTED_CLASSIFICATIONS]
