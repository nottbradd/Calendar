"""The database is the source of truth. Publish UI changes only after a commit."""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path

from .model import Entry, SUBJECT_COLOURS, order_key


APPLICATION_ID = 0x5343414C  # SCAL
SCHEMA_VERSION = 1


class StoreError(Exception):
    """A read or write the user should be told about."""


@dataclass(frozen=True)
class Snapshot:
    entries: tuple[Entry, ...]
    colours: dict[str, str]


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
                if db.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
                    raise StoreError("The calendar database did not pass its integrity check.")
                if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise StoreError("The calendar contains a broken subject reference.")
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
            if (app_id, version) == (APPLICATION_ID, SCHEMA_VERSION):
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
            self.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def read(self) -> Snapshot:
        try:
            with self.db:
                self.db.execute("BEGIN")
                subjects = self.db.execute("SELECT key, name, colour FROM subjects ORDER BY name COLLATE NOCASE").fetchall()
                names = {row["key"]: row["name"] for row in subjects}
                colours = {row["name"]: row["colour"] for row in subjects}
                if any(not re.fullmatch(r"#[0-9a-fA-F]{6}", colour) for colour in colours.values()):
                    raise ValueError("Invalid subject colour")
                entries = []
                with closing(self.db.execute("SELECT * FROM entries")) as rows:
                    for row in rows:
                        entry = Entry(
                            id=row["id"], title=row["title"], day=date.fromisoformat(row["day"]),
                            at=time.fromisoformat(row["at"]) if row["at"] is not None else None,
                            subject=names[row["subject_key"]] if row["subject_key"] is not None else "",
                            kind=row["kind"], custom_kind=row["custom_kind"], notes=row["notes"],
                            completed=bool(row["completed"]), revision=row["revision"],
                        )
                        entries.append(entry.cleaned())
            return Snapshot(tuple(sorted(entries, key=order_key)), colours)
        except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
            raise StoreError(f"Could not read the saved calendar. {exc}") from exc

    def _subject_key(self, name: str) -> str | None:
        if not name:
            return None
        key = name.casefold()
        if not self.db.execute("SELECT 1 FROM subjects WHERE key = ?", (key,)).fetchone():
            count = self.db.execute("SELECT count(*) FROM subjects").fetchone()[0]
            self.db.execute("INSERT INTO subjects VALUES (?, ?, ?)", (key, name, SUBJECT_COLOURS[count % len(SUBJECT_COLOURS)]))
        return key

    def save(self, entry: Entry) -> None:
        try:
            entry = entry.cleaned()
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                values = (
                    entry.title, entry.day.isoformat(), entry.at.isoformat(timespec="minutes") if entry.at else None,
                    self._subject_key(entry.subject), entry.kind, entry.custom_kind, entry.notes,
                    int(entry.completed), entry.id,
                )
                if entry.revision == 0:
                    self.db.execute("""
                        INSERT INTO entries (title, day, at, subject_key, kind, custom_kind, notes, completed, id, revision)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """, values)
                else:
                    result = self.db.execute("""
                        UPDATE entries SET title=?, day=?, at=?, subject_key=?, kind=?, custom_kind=?,
                        notes=?, completed=?, revision=revision+1 WHERE id=? AND revision=?
                    """, (*values, entry.revision))
                    self._check_changed(result)
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
        except sqlite3.Error as exc:
            raise StoreError(f"Could not delete this entry. {exc}") from exc

    def close(self) -> None:
        self.db.close()
