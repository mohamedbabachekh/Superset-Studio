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

from .models import ComponentSpec, DatasetDef, MetricDef, TemplateDef
from .registry import Registry
from .semantic_agent import d3_format


class ChartAgent:
    def __init__(self, registry: Registry):
        self.registry = registry

    # ── titles ──────────────────────────────────────────────────────────────────────────────────────────────
    def title(self, component: ComponentSpec, metric: MetricDef, purpose: str) -> str:
        if component.title:
            return component.title
        label = (component.dimension or "").replace("_", " ")
        if purpose == "kpi":
            return metric.name
        if purpose == "trend":
            grain = {"P1D": "daily", "P1W": "weekly", "P1M": "monthly"}[component.options.time_grain or "P1D"]
            return f"{metric.name}, {grain} trend"
        if purpose == "distribution":
            return f"{metric.name} distribution"
        if purpose == "geo":
            return f"{metric.name} by region"
        if purpose == "ranking":
            top = f"Top {component.options.row_limit} · " if component.options.row_limit else ""
            return f"{top}{metric.name} by {label}"
        return f"{metric.name} by {label}" if label else metric.name

    # ── the chart, as the engine stores it ──────────────────────────────────────────────────────────────────
    def form_data(self, component: ComponentSpec, dataset: DatasetDef, metric: MetricDef, engine_dataset_id: int,
                  template: TemplateDef, color_scheme: str, sequential_scheme: str) -> dict[str, Any]:
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
            form.update(metric=metric.key, y_axis_format=number_format, header_font_size=0.4,
                        subtitle=metric.unit, subtitle_font_size=0.15, time_format="smart_date",
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
                        # a score between 3.1 and 3.6 drawn from zero is a flat line that says nothing
                        truncateYAxis=True, y_axis_bounds=[None, None], truncateXAxis=True)
        elif kind == "bar":
            form.update(x_axis=component.dimension, metrics=[metric.key], groupby=[],
                        orientation="horizontal", x_axis_sort=metric.key,
                        x_axis_sort_asc=options.sort == "asc", row_limit=options.row_limit or 100,
                        order_desc=options.sort != "asc", truncate_metric=True, show_empty_columns=True,
                        color_scheme=color_scheme, show_value=True, show_legend=False, rich_tooltip=True,
                        y_axis_format=number_format, y_axis_bounds=[None, None], truncateXAxis=True,
                        comparison_type="values", annotation_layers=[])
        elif kind == "histogram":
            form.update(column=metric.distribution_column, groupby=[], bins=options.bins or 12,
                        normalize=False, cumulative=False, row_limit=10000, color_scheme=color_scheme,
                        show_value=False, show_legend=False, x_axis_title=metric.name,
                        y_axis_title="Observations")
        elif kind == "pie":
            form.update(groupby=[component.dimension], metric=metric.key, row_limit=options.row_limit or 12,
                        sort_by_metric=True, color_scheme=color_scheme, show_labels=True,
                        label_type="key_percent", number_format=number_format, show_legend=True,
                        legendType="scroll", legendOrientation="top", donut=True, innerRadius=40, outerRadius=70)
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
                project_id: str, headline: Optional[float] = None) -> dict[str, Any]:
        form = self.form_data(component, dataset, metric, engine_dataset_id, template, color_scheme,
                              sequential_scheme)
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
            "description": f"{metric.name} — {' '.join(metric.documentation.split())}"[:1000],
            "dashboards": [dashboard_id],
            # where this chart is defined: a SharpSight project and slot, not the engine's editor
            "external_url": f"sharpsight://bi-project/{project_id}/slot/{component.slot}",
        }

    # ── the same component, as a plain query ────────────────────────────────────────────────────────────────
    def query_context(self, component: ComponentSpec, dataset: DatasetDef, metric: MetricDef,
                      engine_dataset_id: int, filters: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        """What the component asks of the data, sent through the engine's own query API. Used to retrieve and
        validate the dashboard's data independently of how any chart plugin builds its request."""
        query: dict[str, Any] = {"filters": list(filters or []), "extras": {"where": "", "having": ""},
                                 "columns": [], "metrics": [metric.key], "orderby": [], "row_limit": 5000,
                                 "time_range": "No filter", "post_processing": []}
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
