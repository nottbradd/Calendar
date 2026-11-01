import sqlite3
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import date, time
from pathlib import Path
from tempfile import TemporaryDirectory

from school_calendar.model import Entry
from school_calendar.storage import Store, StoreError


class StorageTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "calendar.sqlite3"

    def test_lifecycle_and_subject_colours_survive_separate_connections(self):
        original = Entry("Portfolio", date(2026, 10, 4), " Studio   Art ", notes="Bring folio")
        with closing(Store(self.path)) as store:
            store.save(original)
            first = store.read()
            self.assertEqual(first.entries[0].id, original.id)
            colour = first.colours["Studio Art"]
            store.save(replace(first.entries[0], title="Hand in folio", day=date(2026, 10, 5),
                               subject="studio art", at=time(0), notes="Final pages", completed=True))
        with closing(Store(self.path)) as store:
            state = store.read()
            self.assertEqual(state.colours, {"Studio Art": colour})
            item, = state.entries
            self.assertEqual((item.id, item.title, item.day, item.at, item.notes, item.completed, item.revision),
                             (original.id, "Hand in folio", date(2026, 10, 5), time(0), "Final pages", True, 2))
            self.assertEqual(tuple(store.db.execute("SELECT day, at FROM entries").fetchone()), ("2026-10-05", "00:00"))
            store.delete(item)
        with closing(Store(self.path)) as store:
            self.assertEqual(store.read().entries, ())
            store.save(Entry("Next project", date(2027, 2, 1), "STUDIO ART"))
            self.assertEqual(store.read().colours, {"Studio Art": colour})
            self.assertIsNone(store.db.execute("SELECT at FROM entries").fetchone()[0])

    def test_stale_edits_and_deletes_cannot_erase_another_windows_changes(self):
        with closing(Store(self.path)) as first, closing(Store(self.path)) as other:
            first.save(Entry("Draft", date(2026, 11, 1), "English"))
            stale, = other.read().entries
            first.save(replace(stale, notes="Current revision"))
            with self.assertRaises(StoreError):
                other.save(replace(stale, subject="Uncommitted subject"))
            with self.assertRaises(StoreError):
                other.delete(stale)
            state = other.read()
            self.assertEqual(state.entries[0].notes, "Current revision")
            self.assertNotIn("Uncommitted subject", state.colours)

    def test_unrecognised_or_unreadable_file_is_preserved(self):
        for content in (b"", b"not a calendar database"):
            self.path.write_bytes(content)
            with self.assertRaises(StoreError):
                Store(self.path)
            self.assertEqual(self.path.read_bytes(), content)
        self.path.unlink()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TABLE other_project (value TEXT)")
            db.execute("INSERT INTO other_project VALUES ('keep')")
            db.commit()
        with self.assertRaises(StoreError):
            Store(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT value FROM other_project").fetchone(), ("keep",))
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='entries'").fetchone())

    def test_damaged_row_and_newer_schema_report_failure(self):
        with closing(Store(self.path)) as store:
            store.save(Entry("Keep this", date(2026, 11, 1)))
            with store.db:
                store.db.execute("UPDATE entries SET day='invalid'")
            with self.assertRaises(StoreError):
                store.read()
            store.db.execute("PRAGMA user_version=99")
        with self.assertRaises(StoreError):
            Store(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT title FROM entries").fetchone()[0], "Keep this")
