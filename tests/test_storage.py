import sqlite3
import unittest
from unittest.mock import patch
from contextlib import closing
from dataclasses import replace
from datetime import date, time
from pathlib import Path
from tempfile import TemporaryDirectory

from school_calendar.model import Entry, SUBJECT_COLOURS
from school_calendar.storage import APPLICATION_ID, SCHEMA_VERSION, Store, StoreError, database_path


class StorageTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "calendar.sqlite3"

    def test_default_location_reopens_a_calendar_from_before_the_rename(self):
        profile = self.path.parent
        original = profile / "SchoolCalendar" / "calendar.sqlite3"
        with closing(Store(original)) as previous:
            previous.save(Entry("Revision", date(2026, 11, 7), "History", notes="Chapter 4", completed=True))
            expected = previous.read()
        with patch.dict("os.environ", LOCALAPPDATA=str(profile)), closing(Store(database_path())) as renamed:
            self.assertTrue(original.samefile(renamed.path))
            self.assertEqual(renamed.read(), expected)

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

    def make_v1(self, day="2026-11-03"):
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript(f"""
                PRAGMA application_id={APPLICATION_ID};
                PRAGMA user_version=1;
                CREATE TABLE subjects (key TEXT PRIMARY KEY, name TEXT NOT NULL, colour TEXT NOT NULL);
                CREATE TABLE entries (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL CHECK(length(trim(title)) > 0),
                    day TEXT NOT NULL, at TEXT, subject_key TEXT REFERENCES subjects(key),
                    kind TEXT NOT NULL, custom_kind TEXT NOT NULL, notes TEXT NOT NULL,
                    completed INTEGER NOT NULL CHECK(completed IN (0,1)), revision INTEGER NOT NULL CHECK(revision > 0));
                INSERT INTO subjects VALUES ('history', 'History', '#946044');
            """)
            db.execute("INSERT INTO entries VALUES ('kept','Oral',?,'10:15','history','Custom','Tutorial','Bring notes',1,3)", (day,))
            db.execute("INSERT INTO entries VALUES ('untimed','Report',?,NULL,NULL,'Assignment','','',0,1)", (day,))
            db.commit()

    def test_v1_upgrade_keeps_subjects_revisions_and_unknown_durations(self):
        self.make_v1()
        expected = Entry("Oral", date(2026, 11, 3), "History", "Custom", time(10,15), "Bring notes", "Tutorial", True, "kept", 3)
        for _ in range(2):
            with closing(Store(self.path)) as store:
                state = store.read()
                self.assertEqual(state.view, "month")
                self.assertEqual(state.colours, {"History": "#946044"})
                self.assertEqual(state.entries[1], expected)
                self.assertEqual(state.entries[1].time_description, "10:15 · No end time")
                self.assertEqual(state.entries[0].time_label, "All day")
                self.assertEqual(store.db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def test_invalid_v1_data_rolls_back_the_entire_schema_upgrade(self):
        self.make_v1("invalid")
        original = self.path.read_bytes()
        with self.assertRaises(StoreError):
            Store(self.path)
        self.assertEqual(self.path.read_bytes(), original)

    def test_subject_uniqueness_preferences_and_duration_across_connections(self):
        with closing(Store(self.path)) as first, closing(Store(self.path)) as second:
            first.add_subject("  Studio  Art ", SUBJECT_COLOURS[5])
            for name in (" STUDIO art", "studio art ", "  "):
                with self.assertRaises(StoreError):
                    second.add_subject(name, SUBJECT_COLOURS[0])
            with self.assertRaises(StoreError):
                second.add_subject("Discard", "#123456")
            for start, end in ((None, time(10)), (time(10), time(10)), (time(23,30), time(0,30))):
                with self.assertRaises(StoreError):
                    first.save(Entry("Draft", date.today(), "Unused", at=start, end_at=end))
            first.save(Entry("Critique", date.today(), "STUDIO ART", at=time(0), end_at=time(0,1)))
            second.set_view("week")
            state = first.read()
            self.assertEqual(state.view, "week")
            self.assertEqual(state.colours, {"Studio Art": SUBJECT_COLOURS[5]})
            self.assertEqual(state.entries[0].subject, "Studio Art")
            self.assertEqual(state.entries[0].time_label, "00:00–00:01")
        with closing(Store(self.path)) as reopened:
            self.assertEqual(reopened.read(), state)
