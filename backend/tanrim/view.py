"""How a record is SHOWN, without the environment knowing what one is.

A record's fields belong to the plugin that defined them: a dossier with
cited prices, a job posting, a port survey. The app is a compiled binary and
cannot learn any of that, so a plugin cannot ship rendering code — whatever it
sends has to be data.

So a view is a list of BLOCKS: a small, fixed vocabulary the app knows how to
draw. A plugin answers `Plugin.record_view` with them and gets exactly the
layout it wants; a plugin that answers nothing gets one inferred from the JSON,
which is why an early plugin has a usable record window before anyone has
written a line of presentation.

## Why the vocabulary is small

Every block type is code in a binary that ships on its own schedule, so adding
one is a release. Variety therefore lives in a per-value `format` rather than
in new block types: a colour swatch and a byte count are `fields` with a
format, not two more blocks to maintain.

## Why `source` is a property of a row rather than a block

The web agency's dossier rule is that every fact carries a source URL and
anything uncited goes in `unverified`. That is a rule about FACTS, so the
citation belongs to the row, the list item and the table row — and then a
renderer can mark which facts are cited and which are not, for any plugin,
without being told what a dossier is. A rule that lived in a prompt becomes
something you can see.

`conflict` is there for the same reason: the dossier records that two sources
disagreed about closing time rather than silently picking one, and a row that
knows it is contested can say so.

## Unknown blocks degrade

An app older than the plugin it is talking to renders a block it does not know
as `raw` rather than dropping it — the same rule the live socket follows for a
frame it has never seen. Nothing a plugin sends should be able to make part of
a record invisible.
"""
from __future__ import annotations

from typing import Any

#: Block types an app is expected to draw. Anything else renders as `raw`.
BLOCKS = (
    "section",   # title + note + children
    "text",      # a paragraph; `tone` is normal | quiet | warn
    "fields",    # rows of {label, value, format?, source?, conflict?, note?}
    "list",      # items of {text, source?, done?}; `style` bullets|chips|checks
    "table",     # {columns: [...], rows: [{cells: [...], source?}]}
    "images",    # items of {url, caption?}
    "timeline",  # built by the core from the record's history
    "raw",       # anything that is not worth a shape of its own
)

#: How a single value should read. Not block types: a colour swatch and a byte
#: count are the same ROW with different formatting, and making them blocks
#: would be two more renderers to keep.
FORMATS = ("text", "money", "bytes", "colour", "url", "datetime", "percent")


def section(title: str, children: list[dict], note: str = "") -> dict:
    return {"block": "section", "title": title, "note": note,
            "children": children}


def text(body: str, tone: str = "normal") -> dict:
    return {"block": "text", "body": body, "tone": tone}


def fields(rows: list[dict], title: str = "") -> dict:
    return {"block": "fields", "title": title, "rows": rows}


def row(label: str, value: Any, fmt: str = "text", source: str = "",
        conflict: bool = False, note: str = "") -> dict:
    out: dict[str, Any] = {"label": label, "value": value}
    if fmt != "text":
        out["format"] = fmt
    if source:
        out["source"] = source
    if conflict:
        out["conflict"] = True
    if note:
        out["note"] = note
    return out


def listing(items: list[dict], title: str = "", style: str = "bullets") -> dict:
    return {"block": "list", "title": title, "style": style, "items": items}


def table(columns: list[str], rows: list[dict], title: str = "") -> dict:
    return {"block": "table", "title": title, "columns": columns, "rows": rows}


def images(items: list[dict], title: str = "") -> dict:
    return {"block": "images", "title": title, "items": items}


def raw(value: Any, title: str = "") -> dict:
    return {"block": "raw", "title": title, "value": value}


# ---------------------------------------------------------------------------
# The timeline, which is the core's and not a plugin's
# ---------------------------------------------------------------------------

def timeline(record: dict[str, Any]) -> dict[str, Any]:
    """Where the record has been, from the state machine's own record.

    Built here rather than offered as a block a plugin may emit, because the
    history IS the machine: every move went through `advance_record`, which
    appends to it in the same write. A plugin cannot know it better and should
    not have to restate it, and a plugin that simply forgot would leave the
    one part of a record that is always answerable unanswered.

    Each step carries the rooms it passed through, derived from the agent that
    took it rather than stored, so it stays true when a plugin moves a job
    between rooms.
    """
    from . import castles as geom
    from . import rooms as rooms_mod

    by_id = {r.id: r.name for r in _rooms_safely(rooms_mod)}
    steps: list[dict[str, Any]] = []
    for entry in record.get("history") or []:
        agent = str(entry.get("agent") or "")
        room = _room_of(agent, rooms_mod, geom)
        steps.append({
            "ts": entry.get("ts"),
            "from_stage": entry.get("from_stage"),
            "stage": entry.get("stage"),
            "agent": agent,
            "room": room,
            # The name as well as the id: the id is what opens the room's
            # window, the name is what a person reads.
            "room_name": by_id.get(room, ""),
            "note": entry.get("note") or "",
            # Only present on steps taken since the field existed; the app
            # leaves the line out rather than claiming a step produced nothing.
            "wrote": entry.get("wrote"),
            "by_hand": bool(entry.get("off_table")),
        })
    return {"block": "timeline", "title": "History", "steps": steps}


def _rooms_safely(rooms_mod: Any) -> list:
    try:
        return rooms_mod.load_rooms()
    except Exception:          # noqa: BLE001 — a view must never fail to draw
        return []


def _room_of(agent: str, rooms_mod: Any, geom: Any) -> str:
    """Which room an agent worked in.

    The agent on a history entry may be a WORKER — `probe-4`, or `forge@c7f2`
    — rather than the role a room is staffed by, so both suffixes come off
    before asking.
    """
    if not agent or agent in {"operator", "system"}:
        return ""
    role = geom.base(agent).rsplit("-", 1)[0] \
        if geom.base(agent)[-1:].isdigit() and "-" in geom.base(agent) \
        else geom.base(agent)
    try:
        return rooms_mod.room_for_role(role) or ""
    except Exception:          # noqa: BLE001 — a view must never fail to draw
        return ""


# ---------------------------------------------------------------------------
# What to show when a plugin says nothing
# ---------------------------------------------------------------------------

#: Fields the environment owns. They are the record's plumbing, not its
#: content, and the window shows them in its own header.
MACHINERY = frozenset({
    "id", "ts", "updated_ts", "kind", "stage", "history", "castle_id",
    "name", "source", "fingerprint",
})

_HEX = frozenset("0123456789abcdefABCDEF")


def _format_of(key: str, value: Any) -> str:
    """How a value should read, guessed from it and its name."""
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            return "url"
        if value.startswith("#") and 4 <= len(value) <= 9 \
                and all(c in _HEX for c in value[1:]):
            return "colour"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        low = key.lower()
        if low.endswith(("_usd", "_eur", "_cost", "cost_usd", "price")):
            return "money"
        if low.endswith(("_kb", "_bytes", "bytes")):
            return "bytes"
        # Epoch seconds, roughly 2001 onward. Anything smaller is a count.
        if low.endswith(("_ts", "_at")) and value > 10**9:
            return "datetime"
    return "text"


def _label(key: str) -> str:
    return key.replace("_", " ").strip().capitalize()


def _scalar(v: Any) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def infer(record: dict[str, Any], skip: "frozenset[str]" = MACHINERY
          ) -> list[dict[str, Any]]:
    """A view derived from the record's own shape.

    Deliberately unopinionated: it can tell a list of sentences from a list of
    rows, and a colour from a number, but it cannot know that `must_not_lose`
    is the list that matters most. That is what `Plugin.record_view` is for —
    this is the floor, so that a plugin with no view at all still opens.
    """
    blocks: list[dict[str, Any]] = []
    flat: list[dict[str, Any]] = []

    for key in sorted(record):
        if key in skip:
            continue
        value = record[key]
        if value is None or value == [] or value == {}:
            continue
        if _scalar(value):
            flat.append(row(_label(key), value, _format_of(key, value)))
        else:
            blocks.append(_of(_label(key), value))

    if flat:
        blocks.insert(0, fields(flat, title="Details"))
    return blocks


def _of(title: str, value: Any, depth: int = 0) -> dict[str, Any]:
    """One block for one value, by its shape."""
    if isinstance(value, list):
        if all(_scalar(v) for v in value):
            return listing([{"text": str(v)} for v in value], title=title)
        if all(isinstance(v, dict) for v in value):
            # A uniform list of objects is a table; a ragged one is not, and
            # forcing it into columns would hide whatever made it ragged.
            columns: list[str] = []
            for item in value:
                for k in item:
                    if k not in columns and _scalar(item[k]):
                        columns.append(k)
            if columns and len(columns) <= 6:
                return table(
                    [_label(c) for c in columns],
                    [{"cells": [item.get(c) for c in columns],
                      # The dossier's rule, made visible: a row that cites its
                      # source is marked, and one that does not is marked by
                      # the absence.
                      **({"source": item["source_url"]}
                         if isinstance(item.get("source_url"), str) else {}),
                      } for item in value],
                    title=title)
        return raw(value, title=title)

    if isinstance(value, dict):
        rows = [row(_label(k), v, _format_of(k, v))
                for k, v in sorted(value.items()) if _scalar(v) and v not in (None, "")]
        nested = [_of(_label(k), v, depth + 1)
                  for k, v in sorted(value.items())
                  if not _scalar(v) and v not in (None, [], {})]
        if depth >= 2:
            return raw(value, title=title)
        children: list[dict[str, Any]] = []
        if rows:
            children.append(fields(rows))
        children.extend(nested)
        return section(title, children) if children else raw(value, title=title)

    return raw(value, title=title)
