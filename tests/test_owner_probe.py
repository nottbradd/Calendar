"""Exercise isolation and accounting with the real migrations, without Worker or provider credentials."""

import sqlite3
import unittest
from unittest.mock import patch

from backend.ai import owner_probe as probe


class ProbePolicyTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        for migration in sorted((probe.ROOT / "backend/ai/migrations").glob("*.sql")):
            self.db.executescript(migration.read_text("utf-8"))
        self.member, self.digest = "a" * 32, "d" * 64

    def run_sql(self, sql):
        return [dict(r) for r in self.db.execute(sql)]

    def stage(self):
        probe.stage(self.run_sql, self.member, self.digest, 15_323_000, False)

    def enroll(self):
        self.run_sql("INSERT INTO invites SELECT invite_hash,member,expires FROM owner_probe")
        self.run_sql(
            "INSERT INTO devices SELECT 'new-device',member,'fake-verifier',invite_hash,0 FROM owner_probe"
        )
        self.run_sql("UPDATE owner_probe SET phase='running'")

    def attempt(self, turn, model, seconds, step=0, day="2031-04-14"):
        self.db.execute(
            """INSERT INTO calls(member,turn,step,device,day,model,seconds,is_plan,charge)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
            (self.member, turn, step, "new-device", day, model, seconds, int(model == "gpt-6.1-sol"), 10000),
        )

    def test_interruption_and_403_cleanup_preserve_custom_policy_and_every_hold(self):
        self.run_sql("UPDATE policy SET turns=9,seats=12,seconds=27")
        self.run_sql("UPDATE totals SET amount=1234 WHERE bucket='lifetime'")
        before = probe.snapshot(self.run_sql)
        self.stage()
        self.assertEqual(probe.snapshot(self.run_sql)["policy"], {"id": 1, "paused": 1, **probe.LIMITS})
        with self.assertRaises(sqlite3.IntegrityError):
            self.run_sql("INSERT INTO invites VALUES('different','other',unixepoch()+60)")
        with self.assertRaises(sqlite3.IntegrityError):
            self.run_sql("UPDATE policy SET paused=0")
        self.enroll()
        with self.assertRaises(sqlite3.IntegrityError):
            self.run_sql("DELETE FROM owner_probe")
        with self.assertRaises(sqlite3.IntegrityError):
            self.run_sql("UPDATE policy SET day_cap=999999999")
        with self.assertRaises(sqlite3.IntegrityError):
            self.attempt("plan", "gpt-6.1-sol", 0)
        self.attempt("chat", "gpt-6-luna", 0)
        self.attempt("clip", "gpt-transcribe", 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.attempt("clip2", "gpt-transcribe", 1, day="2031-04-15")
        with self.assertRaises(sqlite3.IntegrityError):
            self.attempt("turn2", "gpt-6-luna", 0, day="2031-04-15")
        self.run_sql("UPDATE owner_probe SET expires=unixepoch()-1")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "service_paused"):
            self.attempt("expired", "gpt-6-luna", 0)

        def failed_network_check():
            self.assertEqual(self.run_sql("SELECT paused FROM policy")[0]["paused"], 1)
            self.assertEqual(self.run_sql("SELECT revoked FROM members")[0]["revoked"], 1)
            raise OSError("simulated lost read")

        with self.assertRaises(OSError):
            probe.close_probe(self.run_sql, self.member, failed_network_check)
        after = probe.snapshot(self.run_sql)
        self.assertEqual(after["policy"], before["policy"])
        self.assertEqual((after["identities"], after["invitations"], after["probe_locks"]), (0, 0, 0))
        self.assertEqual((after["held_nusd"], after["holds"], after["totals"]["lifetime"]), (20000, 2, 21234))
        self.assertTrue(self.run_sql("SELECT id FROM devices WHERE id='new-device'"))

    def test_preexisting_hold_competes_for_headroom_and_survives(self):
        self.stage()
        self.enroll()
        self.attempt("uncertain", "gpt-6-luna", 0)
        probe.close_probe(self.run_sql, self.member)
        self.member = "b" * 32
        self.db.execute("UPDATE totals SET amount=? WHERE bucket='lifetime'", (probe.CAP - 15_323_000 + 1,))
        before = probe.snapshot(self.run_sql)
        with self.assertRaises(probe.Stopped):
            probe.headroom(before, {"max": 15_323_000})
        with self.assertRaises(probe.Stopped):
            self.stage()
        self.assertEqual(probe.snapshot(self.run_sql)["holds"], 1)
        self.assertEqual(probe.snapshot(self.run_sql)["policy"], before["policy"])
        self.assertEqual(probe.snapshot(self.run_sql)["totals"], before["totals"])

    def test_refuse_open_service_other_member_or_invite_and_recover_before_enrollment(self):
        for setup, undo in [
            ("UPDATE policy SET paused=0", "UPDATE policy SET paused=1"),
            ("INSERT INTO members VALUES('other',0)", "DELETE FROM members"),
            ("INSERT INTO invites VALUES('other','other',unixepoch()+60)", "DELETE FROM invites"),
        ]:
            self.run_sql(setup)
            with self.assertRaises((probe.Stopped, sqlite3.IntegrityError)):
                self.stage()
            self.run_sql(undo)
        self.stage()
        self.run_sql("INSERT INTO invites SELECT invite_hash,member,expires FROM owner_probe")
        probe.close_probe(self.run_sql, self.member)
        restored = probe.snapshot(self.run_sql)
        self.assertEqual(
            (restored["identities"], restored["invitations"], restored["probe_locks"]), (0, 0, 0)
        )

    def test_text_denial_still_attempts_audio_once_and_vice_versa(self):
        for text_ok, audio_ok in ((False, True), (True, False)):
            worker = probe.Worker(1)

            def request(route, body, headers=None, text_ok=text_ok, audio_ok=audio_ok, worker=worker):
                ok = text_ok if route == "chat" else audio_ok
                worker.requests[route].append(200 if ok else 503)
                return (200, {"text": "Tuesday"}) if ok else (503, {"error": "upstream_unavailable"})

            def lookup(store, respond):
                for _ in range(2):
                    respond({"input": []})

            with patch.object(worker, "request", request), patch.object(probe, "fictional_lookup", lookup):
                result = probe.endpoints(worker, None, b"\0\0" * 16000)
            self.assertEqual((result["lookup_passed"], result["transcription_passed"]), (text_ok, audio_ok))
            self.assertEqual(len(worker.requests["chat"]), 2 if text_ok else 1)
            self.assertEqual(len(worker.requests["transcribe"]), 1)
