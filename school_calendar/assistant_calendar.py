"""Local read tools and reviewable, atomic calendar proposals."""

from dataclasses import dataclass, replace
from datetime import date, timedelta
import hashlib
import json
import sqlite3
from uuid import uuid4

from .model import Entry, KINDS, parse_time
from .storage import StoreError
from .sync_journal import entry_value, selected_scope

TEXT = {"title": 240, "notes": 2000, "subject": 120, "custom_kind": 80}
FIELDS = (*TEXT, "kind", "day", "at", "end_at", "completed")
STALE = "The calendar changed. Ask for a fresh proposal before applying."


@dataclass(frozen=True)
class Plan:
    key: str
    scope: tuple
    version: str
    edits: tuple
    review: str


def calendar_scope(store):
    row = selected_scope(store.db)
    profile = store.db.execute("SELECT device FROM sync_profile").fetchone()
    return (str(store.path.resolve()), row["anchor"] if row else "", profile[0] if profile else "")


def version(store):
    # Fingerprint the projection (including subjects) locally. A sync or another
    # window changing availability or subject visibility invalidates the preview.
    digest = hashlib.sha256()
    for sql in ("SELECT id,revision FROM entries ORDER BY id", "SELECT * FROM subjects ORDER BY key"):
        for row in store.db.execute(sql):
            digest.update(json.dumps(tuple(row), ensure_ascii=True).encode())
    return digest.hexdigest()


def minutes(value):
    return value.hour * 60 + value.minute


def span(entry):
    if not entry.at:
        return None
    return minutes(entry.at), minutes(entry.end_at) if entry.end_at else 1440


def display(value):
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "Completed" if value else "Open"
    return json.dumps(value, ensure_ascii=False)[1:-1]


LABELS = {
    "day": "Date",
    "at": "Start",
    "end_at": "End",
    "custom_kind": "Custom type",
    "completed": "Status",
    "kind": "Type",
}


class CalendarTools:
    def __init__(self, store, today=None, request_id=None):
        self.store = store
        self.today = today or date.today()
        self.request_id = request_id or uuid4().hex
        self.seen = {}
        self.ambiguous = set()
        with store.db:
            store.db.execute("BEGIN")
            self.scope = calendar_scope(store)
            self.version = version(store)
            self.snapshot = store._snapshot()
        self.entries = {e.id: e for e in self.snapshot.entries}

    def query(self, filters):
        if not isinstance(filters, dict) or set(filters) - {
            "id",
            "title",
            "subject",
            "kind",
            "start",
            "end",
            "completed",
            "details",
        }:
            raise ValueError("Unknown search filter.")
        f = {k: v for k, v in filters.items() if v is not None}
        for key, val in f.items():
            if key in ("completed", "details"):
                if type(val) is not bool:
                    raise ValueError("Expected a yes/no filter.")
            elif not isinstance(val, str) or not 0 < len(val) <= 240:
                raise ValueError("Invalid search text.")
        if not set(f) - {"completed", "details"}:
            raise ValueError("Specify a date range, title, subject, type or entry ID.")
        start = date.fromisoformat(f["start"]) if "start" in f else date.min
        end = date.fromisoformat(f["end"]) if "end" in f else date.max
        if start > end:
            raise ValueError("Invalid date range.")
        matches = [
            e
            for e in self.entries.values()
            if start <= e.day <= end
            and ("id" not in f or f["id"] == e.id)
            and ("completed" not in f or f["completed"] == e.completed)
            and all(
                f.get(k, "").casefold() in val.casefold()
                for k, val in (("title", e.title), ("subject", e.subject), ("kind", e.type_label))
            )
        ]
        titles = {}
        for entry in matches:
            titles.setdefault(entry.title.casefold(), []).append(entry.id)
        if not f.get("id"):
            for siblings in titles.values():
                if len(siblings) > 1:
                    self.ambiguous.update(siblings)
                else:
                    self.ambiguous.discard(siblings[0])
        result = []
        for e in matches[:20]:
            self.seen[e.id] = e
            item = entry_value(e)
            item.update(
                id=e.id, days_remaining=(e.day - self.today).days, ambiguous_title=e.id in self.ambiguous
            )
            if not (f.get("details") and f.get("id") == e.id):
                del item["notes"]
            for k, v in item.items():
                if isinstance(v, str):
                    item[k] = v[:500]
            result.append(item)
        return {"entries": result, "truncated": len(matches) > len(result)}

    def availability(self, day, start, end, end_day=None):
        if end_day is not None and end_day != day:
            first, last = date.fromisoformat(day), date.fromisoformat(end_day)
            days = (last - first).days + 1
            if not 1 <= days <= 31:
                raise ValueError("Choose a range of 1 to 31 days.")
            return {
                "days": {
                    str(first + timedelta(days=i)): self.availability(
                        str(first + timedelta(days=i)), start, end
                    )
                    for i in range(days)
                }
            }
        day = date.fromisoformat(day)
        start, end = parse_time(start), parse_time(end)
        if not start or not end or start >= end:
            raise ValueError("Choose a start and later end on one day.")
        lo, hi = minutes(start), minutes(end)
        blocks = sorted(span(e) for e in self.entries.values() if e.day == day and e.at and not e.completed)
        free = []
        for a, b in blocks:
            if b <= lo or a >= hi:
                continue
            if lo < a:
                free.append((lo, a))
            lo = max(lo, b)
        if lo < hi:
            free.append((lo, hi))
        return {
            "free": [[f"{m // 60:02}:{m % 60:02}" for m in pair] for pair in free],
            "untimed_count": sum(
                e.day == day and not e.at and not e.completed for e in self.entries.values()
            ),
            "policy": "Untimed assessments have no reserved hours. Unknown end times reserve the rest of the day.",
        }

    def propose(self, actions, fixed_ids=()):
        if not isinstance(actions, list) or not 1 <= len(actions) <= 12:
            raise ValueError("Propose between one and twelve changes.")
        if (
            not isinstance(fixed_ids, (list, tuple))
            or len(fixed_ids) > 20
            or any(type(i) is not str or i not in self.seen for i in fixed_ids)
        ):
            raise ValueError("Find each fixed appointment first.")
        edits, lines, changed = [], [], set()
        final = dict(self.entries)
        targets = {a["id"] for a in actions if isinstance(a, dict) and isinstance(a.get("id"), str)}
        for action in actions:
            if not isinstance(action, dict) or set(action) - {"op", "id", "changes"}:
                raise ValueError("Malformed calendar action.")
            op, identifier, fields = action.get("op"), action.get("id"), action.get("changes", {})
            if not isinstance(fields, dict) or fields.keys() - set(FIELDS):
                raise ValueError("Unknown entry field.")
            if op == "create":
                if identifier or not fields.get("day") or not fields.get("title"):
                    raise ValueError("A new assessment needs a title and a specific date.")
                old, new = None, Entry("", self.today)
            else:
                if (
                    not isinstance(identifier, str)
                    or identifier not in self.seen
                    or identifier in changed
                    or identifier in fixed_ids
                ):
                    raise ValueError("Find a unique editable entry first and keep fixed entries unchanged.")
                if identifier in self.ambiguous and any(
                    other.title.casefold() == self.seen[identifier].title.casefold()
                    and other.id not in targets
                    for other in self.entries.values()
                ):
                    raise ValueError(
                        "Several entries share that title. Ask which subject or date, then narrow the search."
                    )
                old = new = self.seen[identifier]
            if op in ("create", "edit"):
                values = dict(fields)
                for key, val in values.items():
                    if key == "completed":
                        if type(val) is not bool:
                            raise ValueError("Invalid completion state.")
                    elif key in ("at", "end_at"):
                        if val is not None and not isinstance(val, str):
                            raise ValueError("Invalid time.")
                        values[key] = parse_time(val or "")
                    elif key == "day":
                        if not isinstance(val, str):
                            raise ValueError("Choose a specific date.")
                        values[key] = date.fromisoformat(val)
                    elif key == "kind":
                        if val not in KINDS:
                            raise ValueError("Unknown assessment type.")
                    elif not isinstance(val, str) or len(val) > TEXT[key]:
                        raise ValueError("Invalid or oversized entry text.")
                new = replace(new, **values).cleaned()
                if new.kind == "Custom" and not new.custom_kind:
                    raise ValueError("Give the custom type a name.")
            elif op in ("complete", "reopen", "delete") and not fields:
                new = None if op == "delete" else replace(old, completed=op == "complete")
            else:
                raise ValueError("Unsupported calendar change.")
            if old == new:
                raise ValueError("The entry already has those values.")
            identifier = (old or new).id
            changed.add(identifier)
            if new:
                final[identifier] = new
            else:
                final.pop(identifier)
            before, after = entry_value(old) if old else {}, entry_value(new) if new else {}
            lines.append(
                f"{op.capitalize()} · {display((old or new).title)} · {(old or new).day.isoformat()}"
            )
            for k in after or before:
                if old and new and before[k] == after[k]:
                    continue
                if (not old or not new) and (after or before)[k] in ("", None):
                    continue
                value = display(after[k]) if new else display(before[k])
                if old and new:
                    value = display(before[k]) + " → " + value
                lines.append(f"  {LABELS.get(k, k.title())}: {value}")
            edits.append((old, new))
        for old, new in edits:
            if not new or new.completed or not new.at:
                continue
            if old and not old.completed and (old.day, old.at, old.end_at) == (new.day, new.at, new.end_at):
                continue
            a, b = span(new)
            for other in final.values():
                if other.id != new.id and other.day == new.day and other.at and not other.completed:
                    c, d = span(other)
                    if a < d and c < b:
                        raise ValueError(
                            "The proposed time overlaps an occupied period. Choose another time."
                        )
        key = hashlib.sha256((self.request_id + json.dumps(actions, sort_keys=True)).encode()).hexdigest()
        return Plan(key, self.scope, self.version, tuple(edits), "\n".join(lines))

    def apply(self, plan):
        db = self.store.db
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                if calendar_scope(self.store) != plan.scope:
                    raise StoreError(STALE)
                receipt = "assistant.applied." + plan.key
                if db.execute("SELECT 1 FROM preferences WHERE key=?", (receipt,)).fetchone():
                    return False
                if version(self.store) != plan.version:
                    raise StoreError(STALE)
                for old, new in plan.edits:
                    if new is None:
                        self.store._delete_entry(old)
                    else:
                        self.store._save_entry(new.cleaned())
                db.execute("INSERT INTO preferences(key,value) VALUES (?, '1')", (receipt,))
        except (sqlite3.Error, ValueError) as exc:
            raise StoreError(f"No changes were applied. {exc}") from exc
        return True
