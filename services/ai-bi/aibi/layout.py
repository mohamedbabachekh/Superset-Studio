"""Where a dashboard's charts sit: the specification's layout when it carries one, the template's otherwise.

Until the Studio there was one answer — the template's rows, applied again at every build, so a row moved in
Superset's own dashboard editor was named and undone (docs/ai-bi.md §9). A canvas cannot live with that: what
a person drags has to be what the next build draws. So a specification may carry a layout OF ITS OWN, and
`resolve` is the one place that says which of the two is in force.

It is carried in the TEMPLATE's own shape (`LayoutRow`, `SlotPlacement`) because that shape is Superset's: a
dashboard is rows of charts, each with a width in twelfths of the grid and the row's height in grid units
(`dashboard_agent.layout` writes exactly that). Superset stores no free coordinates, so none are offered —
and a layout that went out and came back loses nothing.

What a canvas may do is the whole of `problems()`:

  · reorder the rows the template declares, and reorder the charts on a row;
  · move a chart to another row OF ITS OWN SECTION;
  · change a chart's width and a row's height, within the grid and the template's minimum widths.

What it may not do, and why:

  · invent a row — a section owns whole rows (`TemplateSection`), so a row from nowhere belongs to no
    section, has no owner and cannot be announced;
  · move a chart across a section — it would change what the chart is accountable to, and sections have
    owners and filters of their own;
  · scatter a section's rows — a section is announced by one header above its first row, so its rows stay
    together or the header stops meaning anything.

A specification that carries no layout is laid out by its template, exactly as before this file existed, so
every version stored until now restores as it was.
"""
from __future__ import annotations

from collections import Counter
from typing import Optional

from .models import DashboardSpec, LayoutRow, SlotPlacement, TemplateDef


def resolve(spec: DashboardSpec, template: TemplateDef) -> list[LayoutRow]:
    """The rows to draw for this specification: its own layout, or — when it carries none — the template's."""
    return spec.layout or template.layout


def source(spec: DashboardSpec) -> str:
    return "specification" if spec.layout else "template"


def section_of_row(template: TemplateDef, row: str) -> Optional[str]:
    return next((s.id for s in template.section_list() if row in s.rows), None)


def problems(spec: DashboardSpec, template: TemplateDef) -> list[str]:
    """What is wrong with the specification's own layout, in words a person can act on.

    Empty means nothing is — including for a specification that carries no layout, whose layout is the
    template's and was checked when the template was written."""
    if not spec.layout:
        return []
    found: list[str] = []
    declared = [row.row for row in template.layout]
    grid = template.grid.columns
    least = template.responsive.min_slot_width
    where: dict[str, str] = {}                      # slot → the row it was found on
    order: list[str] = []                           # the sections, in the order their rows appear
    found += [f"row {name} appears {count} times" for name, count
              in Counter(row.row for row in spec.layout).items() if count > 1]

    for row in spec.layout:
        if row.row not in declared:
            found.append(f"row {row.row} is not one of {template.id}'s rows ({', '.join(declared)}): a layout "
                         "may reorder the template's rows, not invent one")
            continue
        width = sum(placement.width for placement in row.slots)
        if width > grid:
            found.append(f"row {row.row} is {width} columns wide: the grid has {grid}")
        section = section_of_row(template, row.row)
        if section and (section in order and order[-1] != section):
            found.append(f"section {section} is in two pieces: its rows are announced by one header, so they "
                         "stay together")
        if section and (not order or order[-1] != section):
            order.append(section)
        for placement in row.slots:
            slot = template.slots.get(placement.id)
            if slot is None:
                found.append(f"{placement.id} is not a slot of {template.id}")
                continue
            if placement.id in where:
                found.append(f"{placement.id} is on two rows ({where[placement.id]} and {row.row})")
                continue
            where[placement.id] = row.row
            minimum = least.get(slot.purpose, least.get("chart", 1))
            if placement.width < minimum:
                found.append(f"{placement.id} is {placement.width} columns wide: a {slot.purpose} needs "
                             f"{minimum}")
            belongs = template.section_of(placement.id)
            if belongs != section:
                found.append(f"{placement.id} belongs to section {belongs} and is on a row of {section}: a "
                             "chart does not leave the section it is accountable to")

    for component in spec.components:
        if component.slot not in where:
            found.append(f"{component.slot} has a chart and no place on the layout: it would not be drawn")
    return found


def from_positions(position: dict) -> list[LayoutRow]:
    """Read a layout back out of what Superset holds (`position_json`), as rows of slots.

    The one assertion that makes a canvas safe to build: a layout written for Superset and read back again
    is the layout that went in. It is also how what a person moved in Superset's own dashboard editor could
    one day be adopted instead of undone — the reading is here, the decision is not ours (docs/ai-bi.md §9).

    Rows and charts are named for their slots by `dashboard_agent.layout` (`ROW-<row>`, `CHART-<slot>`), so
    nothing has to be guessed; anything else in the layout — headers, markdown, a tab — is not a row of
    charts and is left alone.
    """
    rows: list[LayoutRow] = []
    for node_id in (position.get("GRID_ID") or {}).get("children", []):
        node = position.get(node_id) or {}
        if node.get("type") != "ROW" or not str(node_id).startswith("ROW-"):
            continue
        slots: list[SlotPlacement] = []
        height = 0
        for child_id in node.get("children", []):
            child = position.get(child_id) or {}
            if child.get("type") != "CHART" or not str(child_id).startswith("CHART-"):
                continue
            meta = child.get("meta") or {}
            slots.append(SlotPlacement(id=str(child_id)[len("CHART-"):], width=int(meta.get("width", 1))))
            height = max(height, int(meta.get("height", 0)))
        if slots:
            rows.append(LayoutRow(row=str(node_id)[len("ROW-"):], height=height, slots=slots))
    return rows
