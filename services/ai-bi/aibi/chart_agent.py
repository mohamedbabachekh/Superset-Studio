"""One specification component → one chart in the BI engine.

The specification says `type: line, metric: cem.qoe_score`. This file is where that becomes the BI engine's
own vocabulary — and it is the ONLY place a chart's engine state is assembled. Every value below comes from a
registry entry or from one of the component's bounded options; nothing arrives here as free text from a
request, which is how « the AI does not generate arbitrary dashboard state » is kept rather than promised.

Metrics are referenced by the name they were saved under on the dataset (SemanticAgent.engine_metric), never
re-typed as a formula in a chart: one definition, on the dataset, with its certification badge.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .models import ComponentOptions, ComponentSpec, DatasetDef, MetricDef, TemplateDef
from .registry import Registry
from .semantic_agent import d3_format


class ChartAgent:
    # up to this many parts, a share-of-total chart names every one of them
    FEW_PARTS = 8

    def __init__(self, registry: Registry):
        self.registry = registry

    # ── titles ──────────────────────────────────────────────────────────────────────────────────────────────
    def title(self, component: ComponentSpec, metric: MetricDef, purpose: str) -> str:
        if component.title:
            return component.title
        # inside a sentence a dimension is lower-case — unless it is an acronym (« by RAT », not « by rat »)
        words = (component.dimension or "").replace("_", " ")
        label = words.upper() if len(words) <= 3 else words
        if purpose == "kpi":
            return metric.name
        if purpose == "trend":
            grain = {"P1D": "daily", "P1W": "weekly", "P1M": "monthly"}[component.options.time_grain or "P1D"]
            return f"{metric.name}, {grain} trend"
        if component.type == "pie":
            return f"{metric.name}, share by {label}"
        if purpose == "distribution":
            return f"{metric.name} distribution"
        if purpose == "geo":
            return f"{metric.name} by region"
        if purpose == "detail" and label:
            # beside a ranking of the same metric by the same dimension, a table titled the same way is two
            # cards with one name (seen on the management template)
            return f"{metric.name}, detail by {label}"
        if purpose == "ranking":
            top = f"Top {component.options.row_limit} · " if component.options.row_limit else ""
            return f"{top}{metric.name} by {label}"
        return f"{metric.name} by {label}" if label else metric.name

    # ── the chart, as the engine stores it ──────────────────────────────────────────────────────────────────
    def form_data(self, component: ComponentSpec, dataset: DatasetDef, metric: MetricDef, engine_dataset_id: int,
                  template: TemplateDef, color_scheme: str, sequential_scheme: str,
                  parts: Optional[int] = None) -> dict[str, Any]:
        """`parts`: for a share-of-total chart, how many parts the dimension has — when it is known."""
        visualization = self.registry.visualizations[component.type]
        options = component.options
        compact_above = float(template.kpi_formatting.get("compact_above", 1e12))
        number_format = d3_format(metric.format, compact=options.number_format == "compact")
        time_column = dataset.time_column
        form: dict[str, Any] = {
            "datasource": f"{engine_dataset_id}__table",
            "viz_type": visualization.engine_type,
            "extra_form_data": {},
            # The dashboard's time filter replaces this comparator. Without the filter the chart reads everything.
            "adhoc_filters": ([{"clause": "WHERE", "expressionType": "SIMPLE", "subject": time_column,
                                "operator": "TEMPORAL_RANGE", "comparator": "No filter"}] if time_column else []),
        }
        kind = component.type
        if kind == "big_number":
            # The engine sizes a headline number as a share of its card, in five steps; the template names a
            # typography token, and the token's size picks the step.
            token = self.registry.tokens.typography[template.typography.get("kpi_value", "display")]
            share = next(s for px, s in ((36, 0.6), (28, 0.5), (22, 0.4), (0, 0.3)) if token["size_px"] >= px)
            form.update(metric=metric.key, y_axis_format=number_format, header_font_size=share,
                        subtitle=metric.unit, subtitle_font_size=0.2, time_format="smart_date",
                        force_timestamp_formatting=False)
            # a headline number too long for its card is shown compact — decided by the template, not here
            form["_compact_above"] = compact_above
        elif kind in ("line", "area"):
            form.update(x_axis=component.dimension or time_column, time_grain_sqla=options.time_grain or "P1D",
                        x_axis_sort_asc=True, metrics=[metric.key], groupby=[], row_limit=10000,
                        order_desc=True, truncate_metric=True, show_empty_columns=True,
                        comparison_type="values", annotation_layers=[], color_scheme=color_scheme,
                        seriesType="line", show_value=False, markerEnabled=False, zoomable=False,
                        show_legend=False, x_axis_time_format="smart_date", rich_tooltip=True,
                        tooltipTimeFormat="smart_date", y_axis_format=number_format,
                        # A score between 3.1 and 3.6 drawn from zero is a flat line that says nothing — so a
                        # LINE's axis is cut to its values. An AREA is read as a quantity: it starts at zero,
                        # or the filled surface lies about how much there is.
                        truncateYAxis=kind == "line", y_axis_bounds=[None, None], truncateXAxis=True)
            if kind == "area":
                form.update(opacity=0.25, stack=None)
        elif kind == "bar":
            # Horizontal bars are drawn from the bottom up, so the FIRST category lands at the bottom: a
            # ranking that should read best-first from the top is therefore sorted ascending for display
            # (seen on the first render, 2026-10-03: the best region was at the bottom). Which rows are
            # fetched is decided separately, by order_desc and row_limit.
            form.update(x_axis=component.dimension, metrics=[metric.key], groupby=[],
                        orientation="horizontal", x_axis_sort=metric.key,
                        x_axis_sort_asc=options.sort != "asc", row_limit=options.row_limit or 100,
                        order_desc=options.sort != "asc", truncate_metric=True, show_empty_columns=True,
                        color_scheme=color_scheme, show_value=True, show_legend=False, rich_tooltip=True,
                        y_axis_format=number_format, y_axis_bounds=[None, None], truncateXAxis=True,
                        comparison_type="values", annotation_layers=[])
        elif kind == "histogram":
            form.update(column=metric.distribution_column, groupby=[], bins=options.bins or 12,
                        normalize=False, cumulative=False, row_limit=10000, color_scheme=color_scheme,
                        show_value=False, show_legend=False, x_axis_title=metric.name,
                        # no y-axis title: this chart has no margin control for it, and the engine draws it
                        # over the tick labels (seen on the first render)
                        x_axis_format=number_format)
        elif kind == "pie":
            # EVERY part is drawn: a pie of the top twelve would show shares of a whole that is not the
            # whole. There is no legend — each slice is named beside it, and a legend of sixteen regions is
            # a scrollbar — so a slice without its label is a slice nobody can name. With few parts every
            # one is labelled, however thin (four technologies, one at 3.99 %: it came out anonymous under
            # a fixed 4 % threshold). With many, the thin ones keep their place and lose their label.
            few = parts is not None and parts <= self.FEW_PARTS
            form.update(groupby=[component.dimension], metric=metric.key, row_limit=options.row_limit or 100,
                        sort_by_metric=True, color_scheme=color_scheme, show_labels=True, labels_outside=True,
                        label_line=True, show_labels_threshold=0 if few else 4,
                        # name over share, on two lines: on one, the longest regions lost their percentage
                        label_type="template", label_template="{name}\n{percent}",
                        # no total in the middle: it is wider than the ring and prints across the slices
                        # (tried); the KPI card beside the chart is where the whole is read
                        number_format=number_format, show_legend=False, donut=True,
                        # a smaller ring than the engine's default: a region's name and its share are long,
                        # and at the default the chart cut them (« Rabat - Salé - Zemmour - Za… »)
                        innerRadius=30, outerRadius=56)
        elif kind == "table":
            form.update(query_mode="aggregate", groupby=[component.dimension], metrics=[metric.key],
                        all_columns=[], percent_metrics=[], order_by_cols=[],
                        timeseries_limit_metric=metric.key, order_desc=options.sort != "asc",
                        row_limit=options.row_limit or 200, table_timestamp_format="smart_date",
                        show_cell_bars=True, color_pn=False, include_search=False, page_length=0,
                        column_config={metric.key: {"d3NumberFormat": number_format}})
        elif kind == "map":
            geo = dataset.columns[component.dimension].geo
            form.update(select_country=geo.country if geo else "", entity=component.dimension,
                        metric=metric.key, number_format=number_format,
                        linear_color_scheme=sequential_scheme, row_limit=5000,
                        # the map is drawn through the engine's older query path, which reads these two
                        granularity_sqla=time_column, time_range="No filter")
        else:
            raise ValueError(f"no chart is defined for the type {kind}")
        return form

    def payload(self, component: ComponentSpec, dataset: DatasetDef, metric: MetricDef, engine_dataset_id: int,
                dashboard_id: int, template: TemplateDef, color_scheme: str, sequential_scheme: str,
                project_id: str, headline: Optional[float] = None, parts: Optional[int] = None) -> dict[str, Any]:
        form = self.form_data(component, dataset, metric, engine_dataset_id, template, color_scheme,
                              sequential_scheme, parts)
        compact_above = form.pop("_compact_above", None)
        if (compact_above is not None and headline is not None and abs(headline) >= compact_above
                and component.options.number_format is None):
            form["y_axis_format"] = d3_format(metric.format, compact=True)
        purpose = template.slots[component.slot].purpose
        return {
            "slice_name": self.title(component, metric, purpose),
            "viz_type": form["viz_type"],
            "datasource_id": engine_dataset_id,
            "datasource_type": "table",
            "params": json.dumps(form, ensure_ascii=False),
            "description": (component.description or
                            f"{metric.name} — {' '.join(metric.documentation.split())}")[:1000],
            "dashboards": [dashboard_id],
            # where this chart is defined: a SharpSight project and slot, not the engine's editor
            "external_url": f"sharpsight://bi-project/{project_id}/slot/{component.slot}",
        }

    # ── a chart, read back from the engine ──────────────────────────────────────────────────────────────────
    _ROW_DEFAULT = {"bar": 100, "table": 200, "pie": 100}

    def read_back(self, component: ComponentSpec, chart: dict[str, Any], purpose: str,
                  datasets_by_engine_id: dict[int, str]) -> dict[str, Any]:
        """A chart as the BI engine holds it NOW — after a person edited it in the engine's own editor — told
        in the specification's vocabulary. `form_data` above is one direction; this is the other.

        What comes back is the component the chart now IS (`component`), what differs from the one specified
        (`changed`), and — when it cannot be a component at all — why (`refused`): a visualization nobody
        approved, an ad-hoc metric in place of a governed one, a dataset that is not registered. Nothing is
        guessed: a chart that is not expressible in governed terms is refused, not approximated."""
        registry = self.registry
        form = json.loads(chart.get("params") or "{}")
        refused: list[str] = []
        kinds = {v.engine_type: kind for kind, v in registry.visualizations.items()}
        viz = form.get("viz_type") or chart.get("viz_type")
        kind = kinds.get(viz)
        if kind is None:
            return {"component": None, "changed": {}, "refused": [f"« {viz} » is not an approved visualization"]}
        by_key = {m.key: m for m in registry.metrics.values()}

        def governed(value: Any) -> Optional[MetricDef]:
            if isinstance(value, dict):
                refused.append(f"an ad-hoc metric (« {value.get('label') or value.get('sqlExpression') or 'custom'} ») "
                               "— a chart shows a governed metric")
                return None
            if value not in by_key:
                refused.append(f"metric « {value} » is not in the metric registry")
                return None
            return by_key[value]

        current = registry.metrics.get(component.metric)
        if kind == "histogram":
            column = form.get("column")
            metric = current if current is not None and current.distribution_column == column else next(
                (m for m in registry.metrics.values() if m.distribution_column == column), None)
            if metric is None:
                refused.append(f"no governed metric has a distribution of « {column} »")
        elif kind in ("big_number", "pie", "map"):
            metric = governed(form.get("metric"))
        else:
            several = form.get("metrics") or []
            if len(several) != 1:
                refused.append(f"one metric per chart, and it has {len(several)}")
            metric = governed(several[0]) if len(several) == 1 else None

        engine_id = str(form.get("datasource", "")).split("__")[0]
        dataset_id = datasets_by_engine_id.get(int(engine_id)) if engine_id.isdigit() else None
        if dataset_id is None:
            refused.append("it reads a dataset that is not one of this project's governed datasets")
        dataset = registry.datasets.get(dataset_id or "")

        dimension: Any = None
        if kind == "bar":
            dimension = form.get("x_axis")
        elif kind in ("pie", "table"):
            columns = form.get("groupby") or []
            if len(columns) != 1:
                refused.append(f"one dimension per {kind}, and it has {len(columns)}")
            dimension = columns[0] if len(columns) == 1 else None
        elif kind == "map":
            dimension = form.get("entity")
        elif kind in ("line", "area"):
            axis = form.get("x_axis")
            if dataset is not None and axis not in (None, dataset.time_column):
                refused.append(f"a {kind} is drawn over time ({dataset.time_column}), not over « {axis} »")
            dimension = component.dimension
        if isinstance(dimension, dict):
            refused.append("an ad-hoc column in place of a dimension of the dataset")
            dimension = None

        options = component.options.model_dump()
        if kind in self._ROW_DEFAULT:
            default = self._ROW_DEFAULT[kind]
            rows = int(form.get("row_limit") or default)
            if rows > 1000:
                refused.append(f"{rows} rows: a chart shows at most 1000")
            options["row_limit"] = None if rows == default else rows
        if kind in ("bar", "table"):
            options["sort"] = "asc" if form.get("order_desc") is False else (None if options.get("sort") != "desc" else "desc")
        if kind in ("line", "area"):
            grain = form.get("time_grain_sqla") or "P1D"
            if grain not in ("P1D", "P1W", "P1M"):
                refused.append(f"time grain « {grain} »: day, week or month")
            elif grain != (options.get("time_grain") or "P1D"):
                options["time_grain"] = grain
        if kind == "histogram":
            bins = int(form.get("bins") or 12)
            if not 4 <= bins <= 40:
                refused.append(f"{bins} bins: between 4 and 40")
            elif bins != (options.get("bins") or 12):
                options["bins"] = bins
        if refused or metric is None or dataset_id is None:
            return {"component": None, "changed": {}, "refused": refused}

        adopted = ComponentSpec(slot=component.slot, type=kind, metric=metric.id, dimension=dimension, dataset=dataset_id,
                                options=ComponentOptions(**options), description=component.description)
        name = chart.get("slice_name") or ""
        adopted.title = None if name == self.title(adopted, metric, purpose) else name[:80]
        changed = {field: {"specification": getattr(component, field), "superset": getattr(adopted, field)}
                   for field in ("type", "metric", "dimension", "dataset")
                   if getattr(component, field) != getattr(adopted, field)}
        # The title is compared as people READ it: the one the specification shows — its own, or the one
        # generated for it — against the one Superset shows. (A shared chart carries its name as its title;
        # compared field by field, that read as « changed » the moment it was created.)
        shown = component.title or (self.title(component, current, purpose) if current is not None else None)
        if name and name != shown:
            changed["title"] = {"specification": shown, "superset": name}
        defaults = {"time_grain": "P1D", "bins": 12, "sort": "desc"}
        for key, value in adopted.options.model_dump().items():
            before = getattr(component.options, key)
            if (before or defaults.get(key)) != (value or defaults.get(key)):
                changed[key] = {"specification": before, "superset": value}
        # a chart that still IS the shared chart it referenced keeps the reference — and its title, as it
        # was written; one that changed does not
        if not changed:
            adopted.title, adopted.asset = component.title, component.asset
        return {"component": adopted, "changed": changed, "refused": []}

    # ── the same component, as a plain query ────────────────────────────────────────────────────────────────
    def query_context(self, component: ComponentSpec, dataset: DatasetDef, metric: MetricDef,
                      engine_dataset_id: int, filters: Optional[list[dict[str, Any]]] = None,
                      time_range: Optional[str] = None) -> dict[str, Any]:
        """What the component asks of the data, sent through the engine's own query API. Used to retrieve and
        validate the dashboard's data independently of how any chart plugin builds its request.

        A dashboard's time filter is NOT one more filter clause. The chart carries its own clause on the time
        column, saying « No filter », and the dashboard sends a query-level `time_range` that replaces it — a
        `time_range` of « No filter » at that level therefore silences any range written in a clause
        (measured against 6.1 on 2026-10-03, after a probe built the other way reported a working filter as
        broken). So: the chart's clause always, and `time_range` only when the dashboard would send one."""
        own = ([{"col": dataset.time_column, "op": "TEMPORAL_RANGE", "val": "No filter"}]
               if dataset.time_column else [])
        query: dict[str, Any] = {"filters": [*own, *(filters or [])], "extras": {"where": "", "having": ""},
                                 "columns": [], "metrics": [metric.key], "orderby": [], "row_limit": 5000,
                                 "post_processing": []}
        if time_range:
            query["time_range"] = time_range
        kind = component.type
        if kind in ("line", "area"):
            query["columns"] = [component.dimension or dataset.time_column]
            query["orderby"] = [[component.dimension or dataset.time_column, True]]
        elif kind in ("bar", "table", "pie"):
            query["columns"] = [component.dimension]
            query["orderby"] = [[metric.key, component.options.sort == "asc"]]
            query["row_limit"] = component.options.row_limit or (100 if kind == "bar" else 200)
        elif kind == "map":
            query["columns"] = [component.dimension]
        elif kind == "histogram":
            query["columns"] = [metric.distribution_column]
            query["metrics"] = []
            query["row_limit"] = 10000
            query["post_processing"] = [{"operation": "histogram", "options": {
                "column": metric.distribution_column, "groupby": [], "bins": component.options.bins or 12,
                "cumulative": False, "normalize": False}}]
        return {"datasource": {"id": engine_dataset_id, "type": "table"}, "force": True, "queries": [query],
                "result_format": "json", "result_type": "full"}
