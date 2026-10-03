"""The governed registries: datasets, metrics, templates, design tokens, visualizations, security, vocabulary.

They are files, read at start and checked against each other. A registry that does not hold together stops
the service rather than serving half of itself: a template naming a colour scheme nobody approved is found
here, not on a customer's screen.

Where a dataset physically lives is NOT in these files. It is read from dbt's own manifest, so the registry
cannot drift from what dbt built.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import ValidationError

from .models import DatasetDef, DesignTokens, MetricDef, Rbac, TemplateDef, Visualization


class RegistryError(Exception):
    pass


def _load(path: Path) -> dict[str, Any]:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RegistryError(f"{path.name}: not valid YAML — {exc}") from exc
    if not isinstance(doc, dict):
        raise RegistryError(f"{path.name}: expected a mapping at the top")
    return doc


def _explain(path: Path, exc: ValidationError) -> RegistryError:
    lines = []
    for err in exc.errors():
        where = ".".join(str(p) for p in err["loc"])
        lines.append(f"{where}: {err['msg']}")
    return RegistryError(f"{path.name}: " + "; ".join(lines))


class Registry:
    def __init__(self, registry_dir: Path, generated_dir: Optional[Path] = None,
                 prod_manifest: Optional[Path] = None, default_schema: str = "bi"):
        self.registry_dir = registry_dir
        self.generated_dir = generated_dir
        self.prod_manifest = prod_manifest
        self.default_schema = default_schema
        self.datasets: dict[str, DatasetDef] = {}
        self.metrics: dict[str, MetricDef] = {}
        self.templates: dict[str, TemplateDef] = {}
        self.visualizations: dict[str, Visualization] = {}
        self.tokens: DesignTokens
        self.rbac: Rbac
        self.lexicon: dict[str, Any] = {}
        self.policies: dict[str, Any] = {}
        self.compatibility: dict[str, Any] = {}
        self.reload()

    # ── loading ─────────────────────────────────────────────────────────────────────────────────────────────
    def reload(self) -> None:
        root = self.registry_dir
        self.datasets = {}
        for path in sorted((root / "datasets").glob("*.yaml")):
            self._add_dataset(path)
        if self.generated_dir and self.generated_dir.exists():
            for path in sorted(self.generated_dir.glob("*.yaml")):
                self._add_dataset(path)

        self.metrics = {}
        for path in sorted((root / "metrics").glob("*.yaml")):
            doc = _load(path)
            try:
                metric = MetricDef(**doc.get("metric", {}))
            except ValidationError as exc:
                raise _explain(path, exc) from exc
            self.metrics[metric.id] = metric

        self.templates = {}
        for path in sorted((root / "templates").glob("*.yaml")):
            try:
                template = TemplateDef(**_load(path))
            except ValidationError as exc:
                raise _explain(path, exc) from exc
            self.templates[template.id] = template

        try:
            self.tokens = DesignTokens(**_load(root / "design" / "tokens.yaml"))
            self.rbac = Rbac(**_load(root / "security" / "rbac.yaml"))
            self.visualizations = {name: Visualization(**body) for name, body in
                                   _load(root / "visualizations.yaml").get("visualizations", {}).items()}
        except ValidationError as exc:
            raise RegistryError(f"registry: {exc}") from exc
        self.lexicon = _load(root / "lexicon.yaml")
        self.policies = _load(root / "policies.yaml")
        matrix = root / "compatibility.yaml"
        self.compatibility = _load(matrix) if matrix.exists() else {}

        self._resolve_physical()
        self._cross_check()

    def _add_dataset(self, path: Path) -> None:
        doc = _load(path)
        try:
            dataset = DatasetDef(**doc.get("dataset", {}), columns=doc.get("columns", {}))
        except ValidationError as exc:
            raise _explain(path, exc) from exc
        if dataset.id in self.datasets:
            raise RegistryError(f"{path.name}: dataset {dataset.id} is defined twice")
        self.datasets[dataset.id] = dataset

    def _resolve_physical(self) -> None:
        """Each dbt-backed dataset's schema, from the production manifest."""
        schemas: dict[str, str] = {}
        if self.prod_manifest and self.prod_manifest.exists():
            manifest = json.loads(self.prod_manifest.read_text(encoding="utf-8"))
            for node in manifest.get("nodes", {}).values():
                if node.get("resource_type") in ("model", "seed"):
                    schemas[node.get("alias") or node["name"]] = node["schema"]
        for dataset in self.datasets.values():
            if dataset.db_schema is None:
                dataset.db_schema = schemas.get(dataset.source_model, self.default_schema)

    def _cross_check(self) -> None:
        problems: list[str] = []
        for metric in self.metrics.values():
            for ds_id in metric.lineage.datasets:
                dataset = self.datasets.get(ds_id)
                if dataset is None:
                    problems.append(f"metric {metric.id}: lineage names an unknown dataset {ds_id}")
                    continue
                for column in metric.lineage.columns:
                    if column not in dataset.columns:
                        problems.append(f"metric {metric.id}: column {column} is not in {ds_id}")
        for dataset in self.datasets.values():
            for name in dataset.grain + dataset.join_keys + [c for c in (dataset.time_column,) if c]:
                if name not in dataset.columns:
                    problems.append(f"dataset {dataset.id}: {name} is named but is not one of its columns")
        tokens = self.tokens
        for template in self.templates.values():
            tid = template.id
            placed = [p.id for row in template.layout for p in row.slots]
            if sorted(placed) != sorted(template.slots):
                problems.append(f"template {tid}: the layout and the slot list do not name the same slots")
            minimum = template.responsive.min_slot_width
            for row in template.layout:
                if sum(p.width for p in row.slots) > template.grid.columns:
                    problems.append(f"template {tid}: row {row.row} is wider than the grid")
                # the narrowest a slot may be, by kind — collapsing only ever widens one, so the check is here
                for placement in row.slots:
                    slot = template.slots.get(placement.id)
                    kind = "kpi" if slot is not None and slot.purpose == "kpi" else "chart"
                    if placement.width < minimum.get(kind, 1):
                        problems.append(f"template {tid}: slot {placement.id} is {placement.width} columns wide, "
                                        f"under the template's own minimum of {minimum[kind]} for a {kind}")
            for slot_id, slot in template.slots.items():
                for kind in slot.accepts:
                    if kind not in self.visualizations:
                        problems.append(f"template {tid}: slot {slot_id} accepts {kind}, which is not approved")
            if template.theme.color_scheme not in tokens.color_schemes:
                problems.append(f"template {tid}: colour scheme {template.theme.color_scheme} is not approved")
            if template.theme.sequential_scheme not in tokens.sequential_schemes:
                problems.append(f"template {tid}: sequential scheme {template.theme.sequential_scheme} is not approved")
            if template.theme.variant not in tokens.theme_variants:
                problems.append(f"template {tid}: theme variant {template.theme.variant} is not approved")
            for use, name in template.typography.items():
                if name not in tokens.typography:
                    problems.append(f"template {tid}: typography {use} → {name} is not a token")
            for use, name in template.spacing.items():
                if name not in tokens.spacing:
                    problems.append(f"template {tid}: spacing {use} → {name} is not a token")
            for use, name in template.colors.items():
                if name not in tokens.colors:
                    problems.append(f"template {tid}: colour {use} → {name} is not a token")
            for component, variant in template.components.items():
                if variant not in tokens.component_variants.get(component, []):
                    problems.append(f"template {tid}: {component} variant {variant} is not approved")
            rows = [row.row for row in template.layout]
            owned = [name for section in template.sections for name in section.rows]
            if template.sections and (sorted(owned) != sorted(rows) or owned != [r for r in rows if r in owned]):
                problems.append(f"template {tid}: its sections must name every row exactly once, in the layout's order")
            if len({s.id for s in template.sections}) != len(template.sections):
                problems.append(f"template {tid}: two sections have the same id")
        for user, body in self.rbac.users.items():
            for role in body.roles:
                if role not in self.rbac.roles:
                    problems.append(f"rbac: user {user} has an unknown role {role}")
        if problems:
            raise RegistryError("the registries do not hold together:\n  " + "\n  ".join(problems))

    # ── reading ─────────────────────────────────────────────────────────────────────────────────────────────
    def dataset(self, dataset_id: str) -> DatasetDef:
        try:
            return self.datasets[dataset_id]
        except KeyError:
            raise RegistryError(f"no dataset is registered as {dataset_id}") from None

    def metric(self, metric_id: str) -> MetricDef:
        try:
            return self.metrics[metric_id]
        except KeyError:
            raise RegistryError(f"no metric is registered as {metric_id}") from None

    def template(self, name: str) -> TemplateDef:
        wanted = name.strip().lower()
        for template in self.templates.values():
            if wanted == template.id or wanted in (a.lower() for a in template.template.aliases):
                return template
        raise RegistryError(f"no dashboard template is registered as {name}")

    def dataset_by_relation(self, schema: Optional[str], table: str) -> Optional[DatasetDef]:
        matches = [d for d in self.datasets.values()
                   if d.source_model == table and (schema is None or d.db_schema == schema)]
        return matches[0] if len(matches) == 1 else None

    # ── what the engine adds ────────────────────────────────────────────────────────────────────────────────
    def register_generated(self, dataset: DatasetDef) -> Path:
        """A dataset the engine built. It is written beside the governed ones, never among them, and it is
        never certified: certification is a decision, and it is not this service's to take."""
        if self.generated_dir is None:
            raise RegistryError("this registry has nowhere to keep generated datasets")
        if dataset.certified:
            raise RegistryError("a generated dataset cannot be registered as certified")
        existing = self.datasets.get(dataset.id)
        if existing is not None and not existing.generated_by:
            raise RegistryError(f"{dataset.id} is a governed dataset and cannot be replaced by a generated one")
        if existing is not None and existing.project != dataset.project:
            raise RegistryError(f"{dataset.id} was generated for BI project {existing.project} and cannot be "
                                f"replaced by {dataset.project}")
        self.generated_dir.mkdir(parents=True, exist_ok=True)
        body = dataset.model_dump(exclude_none=True)
        columns = body.pop("columns")
        path = self.generated_dir / f"{dataset.id}.yaml"
        path.write_text(yaml.safe_dump({"dataset": body, "columns": columns}, sort_keys=False, allow_unicode=True),
                        encoding="utf-8")
        self.datasets[dataset.id] = dataset
        return path

    def unregister_generated(self, dataset_id: str) -> None:
        dataset = self.datasets.get(dataset_id)
        if dataset is None or not dataset.generated_by:
            raise RegistryError(f"{dataset_id} is not a generated dataset")
        (self.generated_dir / f"{dataset_id}.yaml").unlink(missing_ok=True)
        del self.datasets[dataset_id]
