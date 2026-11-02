import json
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from nacl.exceptions import CryptoError
from nacl.public import PrivateKey
from school_calendar import sync_crypto as crypto, sync_journal as journal
from school_calendar.model import Entry
from school_calendar.storage import APPLICATION_ID, SCHEMA_VERSION, Store, StoreError
from school_calendar.sync_client import Sync, summary


class SyncStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "disposable.sqlite3"
        self.store = Store(self.path)
        self.addCleanup(self.store.close)

    def test_atomic_local_edit_outbox_and_local_preferences(self):
        engine = Sync(self.store)
        engine.begin("create", crypto.kit("CALR1"))
        db = self.store.db
        db.execute("CREATE TRIGGER interrupt BEFORE INSERT ON sync_ops BEGIN SELECT RAISE(ABORT,'test interruption'); END")
        with self.assertRaises(StoreError):
            self.store.save(Entry("Disposable", date.today(), "Science"))
        self.assertEqual(self.store.read().entries, ())
        self.assertEqual(self.store.read().colours, {})
        db.execute("DROP TRIGGER interrupt")
        self.store.save(Entry("Disposable", date.today(), "Science"))
        self.store.set_view("week")
        self.assertEqual(summary(db)["pending"], 2)
        self.store.delete(self.store.read().entries[0])
        with closing(Store(self.path)) as reopened:
            self.assertEqual(summary(reopened.db)["pending"], 3)
            self.assertEqual(reopened.read().view, "week")
            self.assertEqual(reopened.read().entries, ())

    def test_out_of_order_duplicate_and_delete_heads(self):
        db = self.store.db
        entry = Entry("First", date.today())
        entity = "e:" + entry.id
        parent, edit, deletion, stale = [uuid4().hex for _ in range(4)]
        def apply(op, parents, item):
            with db:
                journal.receive(db, op, {"entity": entity, "parents": parents,
                                         "value": journal.entry_value(item) if item else None})
        apply(deletion, [edit], None)
        apply(edit, [parent], replace(entry, title="Edited"))
        apply(parent, [], entry)
        apply(edit, [parent], replace(entry, title="Edited"))
        self.assertEqual(self.store.read().entries, ())
        apply(stale, [parent], replace(entry, title="Offline edit"))
        self.assertEqual(self.store.read().entries, ())
        self.assertEqual(journal.conflicts(db), [entity])
        journal.resolve(db, entity, [deletion, stale], deletion)
        self.assertFalse(journal.conflicts(db))
        self.assertEqual(self.store.read().entries, ())
        self.assertEqual(db.execute("SELECT count(*) FROM sync_ops WHERE pending=1").fetchone()[0], 1)

    def test_v2_migration_preserves_legacy_id_and_view_without_enabling_sync(self):
        path = Path(self.temp.name) / "v2.sqlite3"
        with closing(Store(path)) as old:
            old.save(Entry("Existing sample", date.today(), id="older-id"))
            old.set_view("week")
            with old.db:
                for table in ("sync_edges", "sync_ops", "sync_profile"):
                    old.db.execute("DROP TABLE " + table)
                old.db.execute("ALTER TABLE subjects DROP COLUMN hidden")
                old.db.execute("PRAGMA user_version=2")
        with closing(Store(path)) as reopened:
            self.assertEqual(reopened.db.execute("PRAGMA application_id").fetchone()[0], APPLICATION_ID)
            self.assertEqual(reopened.db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(reopened.read().entries[0].id, "older-id")
            self.assertEqual(reopened.read().view, "week")
            self.assertEqual(summary(reopened.db)["phase"], "disabled")

    def test_oversized_local_draft_can_be_shortened_without_blocking_other_items(self):
        Sync(self.store).begin("create", crypto.kit("CALR1"))
        self.store.save(Entry("Large", date.today(), notes="x" * 530000))
        self.store.save(Entry("Small", date.today()))
        self.assertEqual(summary(self.store.db)["oversized"], 1)
        small_uploads = self.store.db.execute("SELECT count(*) FROM sync_ops WHERE pending=1").fetchone()[0]
        self.assertEqual(small_uploads, 1)
        large = next(e for e in self.store.read().entries if e.title == "Large")
        self.store.save(replace(large, notes="Shorter notes"))
        self.assertEqual(summary(self.store.db)["oversized"], 0)
        self.assertEqual(summary(self.store.db)["pending"], 2)

    def test_crypto_context_tampering_wrong_recipient_and_dpapi(self):
        calendar, op = uuid4().hex, uuid4().hex
        keys = {"1": crypto.random_key()}
        ciphertext = crypto.encrypt(calendar, keys, 1, op, {"notes": "disposable"})
        change = {"op": op, "epoch": 1, "box": ciphertext}
        self.assertEqual(crypto.decrypt(calendar, keys, change)["notes"], "disposable")
        with self.assertRaises(CryptoError):
            crypto.decrypt(uuid4().hex, keys, change)
        with self.assertRaises(CryptoError):
            crypto.decrypt(calendar, keys, {**change, "op": uuid4().hex})
        damaged = bytearray(crypto.decode(ciphertext))
        damaged[-1] ^= 1
        with self.assertRaises(CryptoError):
            crypto.decrypt(calendar, keys, {**change, "box": crypto.encode(damaged)})
        private = PrivateKey.generate()
        envelope = crypto.wrap(calendar, crypto.encode(bytes(private.public_key)), keys)
        self.assertEqual(crypto.unwrap(calendar, private, envelope, 1), keys)
        with self.assertRaises(CryptoError):
            crypto.unwrap(calendar, PrivateKey.generate(), envelope, 1)
        engine = Sync(self.store)
        kit = crypto.kit("CALR1")
        engine.begin("create", kit)
        _, ident = engine.profile()
        blob = self.store.db.execute("SELECT secret FROM sync_profile").fetchone()[0]
        self.assertNotIn(ident["token"].encode(), blob)
        self.assertNotIn(ident["private"].encode(), blob)
        self.assertNotIn(kit.split(".")[-1], json.dumps(ident))
        self.assertNotIn(crypto.recovery(kit.split(".")[-1])[0], json.dumps(ident))

    def test_substituted_roster_or_calendar_key_does_not_activate_a_profile(self):
        engine = Sync(self.store)
        engine.begin("create", crypto.kit("CALR1"))
        _, ident = engine.profile()
        initial = ident["setup"]
        me = {"id": ident["device"], "pub": ident["pub"], "tag": initial["tag"]}
        state = {"epoch": 1, "generation": 1, "box": initial["box"], "members": [me], "pending": None,
                 "recoveryPub": initial["recoveryPub"], "recoveryTag": initial["recoveryTag"]}
        fake_member = {**me, "id": uuid4().hex}
        with self.assertRaises(ValueError):
            engine._state(ident, {**state, "members": [me, fake_member]})
        fake_keys = {"1": crypto.random_key()}
        with self.assertRaises(ValueError):
            engine._state(ident, {**state, "box": crypto.wrap(ident["calendar"], ident["pub"], fake_keys)})
        self.assertEqual(engine.profile()[0], "create")
