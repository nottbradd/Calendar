"""Two installations exercising the real encrypted feed with disposable calendars."""
import os
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import date
from multiprocessing import get_context
from pathlib import Path
from tempfile import TemporaryDirectory

from school_calendar import sync_crypto as crypto, sync_journal as journal
from school_calendar.model import Entry, SUBJECT_COLOURS
from school_calendar.storage import Store, StoreError
from school_calendar.sync_client import Api, SERVICE, Sync, summary
from scripts.verify_sync import d1


def recover_after_restart(path, base, kit):
    with closing(Store(path)) as store:
        engine = Sync(store, Api(base))
        engine.begin("recover", kit)
        engine.run()


@unittest.skipUnless(os.environ.get("CALENDAR_LOCAL_UI_TRIAL") == "1", "requires a development Worker")
class SyncRegressionTrials(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        base = os.environ.get("CALENDAR_REGRESSION_BASE", "http://127.0.0.1:18787")
        self.assertIn(base, ("http://127.0.0.1:18787", SERVICE))
        self.api = Api(base)
        self.target = "--remote" if base == SERVICE else "--local"

    def pc(self, name):
        return Sync(self.enterContext(closing(Store(self.folder / name / "calendar.sqlite3"))), self.api)

    def create(self, pc):
        kit = crypto.kit("CALR1")
        calendar, _ = crypto.parse_kit(kit, "CALR1")
        self.addCleanup(self.cleanup_calendar, calendar)
        pc.begin("create", kit)
        pc.run()
        return kit

    def cleanup_calendar(self, calendar):
        d1(self.target, f"DELETE FROM sync_calendars WHERE id='{calendar}'")
        rows = d1(self.target, f"SELECT count(*) AS remaining FROM sync_changes WHERE calendar='{calendar}'")
        self.assertEqual(rows[0]["remaining"], 0)

    def pair(self, owner, other):
        other.begin("join", owner.invite())
        self.assertTrue(other.run()["waiting"])
        _, candidate = other.profile()
        owner.approve(crypto.fingerprint(candidate["device"], candidate["pub"]))
        other.run()

    def test_unsent_deletion_survives_disconnect_restart_and_recovery(self):
        a, b = self.pc("A"), self.pc("B")
        kit = self.create(a)
        a.store.save(Entry("Disposable assessment", date(2031, 4, 17)))
        a.run()
        self.pair(a, b)
        item, = a.store.read().entries
        self.assertEqual(b.store.read().entries[0].id, item.id)
        a.store.delete(item)
        deletion, = journal.heads(a.db, "e:" + item.id)
        a.disconnect()
        self.assertEqual(summary(a.db)["phase"], "disabled")
        a.store.close()

        # Start a fresh Windows interpreter with only the disk path and the recovery
        # key. No open connection, decrypted identity or in-memory journal survives.
        child = get_context("spawn").Process(target=recover_after_restart, args=(a.store.path, self.api.base, kit))
        child.start()
        try:
            child.join(60)
            self.assertEqual(child.exitcode, 0, "Recovery in the restarted installation failed or timed out.")
        finally:
            if child.is_alive():
                child.terminate()
                child.join()
            child.close()
        a = self.pc("A")
        b.run()
        self.assertEqual(a.store.read().entries, ())
        self.assertEqual(b.store.read().entries, ())
        for pc in (a, b):
            head, = journal.heads(pc.db, "e:" + item.id)
            self.assertEqual((head["op"], head["parents"], head["pending"]),
                             (deletion["op"], deletion["parents"], 0))
            self.assertEqual(journal.conflicts(pc.db), [])
        self.assertEqual(a.run()["uploaded"], 0)

    def test_unrelated_calendar_with_same_entry_id_never_receives_retained_changes(self):
        a, b = self.pc("A"), self.pc("B")
        first, second = self.create(a), self.create(b)
        item = Entry("First calendar", date(2031, 4, 17))
        a.store.save(item)
        b.store.save(Entry("Independent assessment", item.day, id=item.id))
        a.run()
        b.run()
        stale = a.store.read().entries[0]
        a.store.delete(stale)
        a.disconnect()
        a.store.save(Entry("Written while disconnected", item.day))
        a.store.close()
        a = self.pc("A")

        a.begin("recover", second)
        self.assertEqual(a.run()["uploaded"], 0)
        with self.assertRaises(StoreError):
            a.store.save(replace(stale, title="Old window"))
        with self.assertRaises(StoreError):
            a.store.delete(stale)
        self.assertEqual(a.run()["uploaded"], 0)
        b.run()
        self.assertEqual([e.title for e in b.store.read().entries], ["Independent assessment"])
        self.assertEqual(journal.conflicts(b.db), [])
        a.disconnect()
        a.begin("recover", first)
        self.assertEqual(a.run()["uploaded"], 2)
        witness = self.pc("Clean recovery")
        witness.begin("recover", first)
        witness.run()
        self.assertEqual([e.title for e in witness.store.read().entries], ["Written while disconnected"])
        self.assertEqual(b.run()["uploaded"], 0)
        self.assertEqual(b.store.read().entries[0].title, "Independent assessment")

    def test_initial_link_preserves_removed_subject_and_restores_label_and_colour_on_both_pcs(self):
        a, b = self.pc("A"), self.pc("B")
        name, colour = "Studio Art", SUBJECT_COLOURS[5]
        a.store.add_subject(name, SUBJECT_COLOURS[2])
        item = Entry("Disposable portfolio", date(2031, 4, 19), "STUDIO ART")
        a.store.save(item)
        a.store.remove_subject(name)
        self.assertEqual(a.store.read().entries[0].subject, "")
        self.create(a)
        self.pair(a, b)
        for pc in (a, b):
            self.assertNotIn(name, pc.store.read().colours)
            self.assertEqual(pc.store.read().entries[0].subject, "")
        a.store.add_subject(name, colour)
        a.run()
        b.run()
        for pc in (a, b):
            view = pc.store.read()
            self.assertEqual((view.entries[0].id, view.entries[0].subject, view.colours[name]),
                             (item.id, name, colour))
            self.assertEqual(journal.conflicts(pc.db), [])
