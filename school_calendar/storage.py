"""The database is the source of truth. Publish UI changes only after a commit."""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import date, time
from pathlib import Path

from .model import Entry, SUBJECT_COLOURS, order_key
from . import sync_journal as journal


APPLICATION_ID = 0x5343414C  # SCAL
SCHEMA_VERSION = 4


class StoreError(Exception):
    """A read or write the user should be told about."""


@dataclass(frozen=True)
class Snapshot:
    entries: tuple[Entry, ...]
    colours: dict[str, str]
    view: str = "month"


def database_path() -> Path:
    local = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    return Path(local) / "SchoolCalendar" / "calendar.sqlite3"


class Store:
    def __init__(self, path: Path):
        self.path = path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            existed = path.exists()
            db = sqlite3.connect(path, timeout=3)
            try:
                self.db = db
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA foreign_keys = ON")
                self._open_schema(existed)
            except BaseException:
                db.close()
                raise
        except (OSError, sqlite3.Error) as exc:
            raise StoreError(f"Could not open the calendar. {exc}") from exc

    def _open_schema(self, existed: bool) -> None:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            app_id = self.db.execute("PRAGMA application_id").fetchone()[0]
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if self.db.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
                raise StoreError("The calendar database did not pass its integrity check.")
            if self.db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise StoreError("The calendar contains a broken subject reference.")
            if (app_id, version) == (APPLICATION_ID, SCHEMA_VERSION):
                return
            if app_id == APPLICATION_ID and version in (1, 2, 3):
                if version == 1:
                    self._add_schedule_schema()
                if version < 3:
                    journal.create_schema(self.db)
                self._add_history_schema()
                # Validate every local row before using any of them for a sync repair.
                self._snapshot()
                if version == 3 and self.db.execute("SELECT 1 FROM sync_profile").fetchone():
                    journal.repair_legacy_subject_links(self)
                return
            if existed or app_id or version:
                raise StoreError("This file is damaged, unrecognised, or belongs to another calendar version.")
            self.db.execute("""
                CREATE TABLE subjects (
                    key TEXT PRIMARY KEY, name TEXT NOT NULL, colour TEXT NOT NULL
                )
            """)
            self.db.execute("""
                CREATE TABLE entries (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL CHECK (length(trim(title)) > 0),
                    day TEXT NOT NULL, at TEXT, subject_key TEXT REFERENCES subjects(key),
                    kind TEXT NOT NULL, custom_kind TEXT NOT NULL, notes TEXT NOT NULL,
                    completed INTEGER NOT NULL CHECK (completed IN (0, 1)),
                    revision INTEGER NOT NULL CHECK (revision > 0)
                )
            """)
            self.db.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            self._add_schedule_schema()
            journal.create_schema(self.db)
            self._add_history_schema()

    def _add_schedule_schema(self) -> None:
        self.db.execute("ALTER TABLE entries ADD COLUMN end_at TEXT CHECK (end_at IS NULL OR (at IS NOT NULL AND end_at > at))")
        self.db.execute("CREATE TABLE preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def _add_history_schema(self) -> None:
        journal.create_history_schema(self.db)
        self.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def read(self) -> Snapshot:
        try:
            with self.db:
                self.db.execute("BEGIN")
                return self._snapshot()
        except sqlite3.Error as exc:
            raise StoreError(f"Could not read the saved calendar. {exc}") from exc

    def stored_entries(self, identifier=None):
        """Read the saved association even when that subject is hidden in the UI."""
        query = "SELECT e.*,s.name AS subject FROM entries e LEFT JOIN subjects s ON s.key=e.subject_key"
        with closing(self.db.execute(query + (" WHERE e.id=?" if identifier is not None else ""),
                                     (identifier,) if identifier is not None else ())) as rows:
            for row in rows:
                yield Entry(
                    id=row["id"], title=row["title"], day=date.fromisoformat(row["day"]),
                    at=time.fromisoformat(row["at"]) if row["at"] is not None else None,
                    end_at=time.fromisoformat(row["end_at"]) if row["end_at"] is not None else None,
                    subject=row["subject"] or "", kind=row["kind"], custom_kind=row["custom_kind"],
                    notes=row["notes"], completed=bool(row["completed"]), revision=row["revision"],
                ).cleaned()

    def _snapshot(self) -> Snapshot:
        try:
            subjects = self.db.execute("SELECT key,name,colour,hidden FROM subjects ORDER BY name COLLATE NOCASE").fetchall()
            colours = {row["name"]: row["colour"] for row in subjects if not row["hidden"]}
            if any(not re.fullmatch(r"#[0-9a-fA-F]{6}", row["colour"]) for row in subjects):
                raise ValueError("Invalid subject colour")
            entries = (replace(entry, subject="") if entry.subject and entry.subject not in colours else entry
                       for entry in self.stored_entries())
            pref = self.db.execute("SELECT value FROM preferences WHERE key='calendar_view'").fetchone()
            return Snapshot(tuple(sorted(entries, key=order_key)), colours, "week" if pref and pref[0] == "week" else "month")
        except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
            raise StoreError(f"Could not read the saved calendar. {exc}") from exc

    def set_view(self, view: str) -> None:
        if view not in ("week", "month"):
            raise StoreError("Choose Month or Week.")
        try:
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO preferences (key, value) VALUES ('calendar_view', ?)", (view,))
        except sqlite3.Error as exc:
            raise StoreError(f"Could not save the calendar view. {exc}") from exc

    def add_subject(self, name: str, colour: str) -> None:
        name = " ".join(name.split())
        if not name:
            raise StoreError("Enter a subject name.")
        if colour not in SUBJECT_COLOURS:
            raise StoreError("Choose a colour from the palette.")
        try:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                key = name.casefold()
                if self.db.execute("SELECT 1 FROM subjects WHERE key=? AND hidden=0", (key,)).fetchone():
                    raise StoreError(f"“{name}” is already in your subjects.")
                self.db.execute("""INSERT INTO subjects(key,name,colour) VALUES (?,?,?) ON CONFLICT(key)
                                   DO UPDATE SET name=excluded.name,colour=excluded.colour,hidden=0""", (key, name, colour))
                journal.record(self.db, "s:" + key, {"name": name, "colour": colour})
        except (sqlite3.Error, ValueError) as exc:
            raise StoreError(f"Could not add this subject. {exc}") from exc

    def _subject_key(self, name: str) -> str | None:
        if not name:
            return None
        key = name.casefold()
        existing = self.db.execute("SELECT hidden FROM subjects WHERE key=?", (key,)).fetchone()
        if existing and existing["hidden"]:
            raise StoreError("This subject was removed. Add it in Settings to use it again.")
        if not existing:
            count = self.db.execute("SELECT count(*) FROM subjects WHERE hidden=0").fetchone()[0]
            colour = SUBJECT_COLOURS[count % len(SUBJECT_COLOURS)]
            self.db.execute("INSERT INTO subjects(key,name,colour) VALUES (?, ?, ?)", (key, name, colour))
            journal.record(self.db, "s:" + key, {"name": name, "colour": colour})
        return key

    def set_subject_colour(self, name: str, colour: str) -> None:
        if colour not in SUBJECT_COLOURS:
            raise StoreError("Choose a colour from the palette.")
        try:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                key = name.casefold()
                row = self.db.execute("SELECT name FROM subjects WHERE key=? AND hidden=0", (key,)).fetchone()
                if not row:
                    raise StoreError("Choose a saved subject.")
                self.db.execute("UPDATE subjects SET colour=? WHERE key=?", (colour, key))
                journal.record(self.db, "s:" + key, {"name": row["name"], "colour": colour})
        except (sqlite3.Error, ValueError) as exc:
            raise StoreError(f"Could not save the subject colour. {exc}") from exc

    def remove_subject(self, name: str) -> None:
        key = name.casefold()
        try:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                result = self.db.execute("UPDATE subjects SET hidden=1 WHERE key=? AND hidden=0", (key,))
                if not result.rowcount:
                    raise StoreError("Choose a saved subject.")
                journal.record(self.db, "s:" + key, None)
        except (sqlite3.Error, ValueError) as exc:
            raise StoreError(f"Could not remove the subject. {exc}") from exc

    def save(self, entry: Entry) -> None:
        try:
            entry = entry.cleaned()
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                subject_key = self._subject_key(entry.subject)
                if subject_key is None and entry.revision:
                    # The editor and completion action see a blank label for a hidden subject.
                    hidden = self.db.execute("""SELECT e.subject_key FROM entries e JOIN subjects s ON s.key=e.subject_key
                                                WHERE e.id=? AND s.hidden=1""", (entry.id,)).fetchone()
                    subject_key = hidden[0] if hidden else None
                values = (
                    entry.title, entry.day.isoformat(), entry.at.isoformat(timespec="minutes") if entry.at else None,
                    entry.end_at.isoformat(timespec="minutes") if entry.end_at else None,
                    subject_key, entry.kind, entry.custom_kind, entry.notes,
                    int(entry.completed), entry.id,
                )
                if entry.revision == 0:
                    self.db.execute("""
                        INSERT INTO entries (title, day, at, end_at, subject_key, kind, custom_kind, notes, completed, id, revision)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """, values)
                else:
                    result = self.db.execute("""
                        UPDATE entries SET title=?, day=?, at=?, end_at=?, subject_key=?, kind=?, custom_kind=?,
                        notes=?, completed=?, revision=revision+1 WHERE id=? AND revision=?
                    """, (*values, entry.revision))
                    self._check_changed(result)
                journal.record(self.db, "e:" + entry.id, journal.entry_value(next(self.stored_entries(entry.id))))
        except (sqlite3.Error, ValueError) as exc:
            raise StoreError(f"Could not save this entry. {exc}") from exc

    @staticmethod
    def _check_changed(result: sqlite3.Cursor) -> None:
        if result.rowcount != 1:
            raise StoreError("This entry changed in another window. Close this view and open the entry again.")

    def delete(self, entry: Entry) -> None:
        try:
            with self.db:
                result = self.db.execute("DELETE FROM entries WHERE id=? AND revision=?", (entry.id, entry.revision))
                self._check_changed(result)
                journal.record(self.db, "e:" + entry.id, None)
        except (sqlite3.Error, ValueError) as exc:
            raise StoreError(f"Could not delete this entry. {exc}") from exc

    def close(self) -> None:
        self.db.close()
