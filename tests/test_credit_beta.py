"""Credit-funded activation uses real SQLite migrations and an isolated, restartable ledger."""

from datetime import UTC, datetime
import io
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend.ai import owner


class CreditBetaTests(unittest.TestCase):
    def setUp(self):
        self.folder = self.enterContext(TemporaryDirectory())
        self.path = Path(self.folder) / "credit.sqlite3"
        self.now = int(datetime(2026, 11, 5, 12, tzinfo=UTC).timestamp())
        self.expires = owner.grant_expiry("2026-11-26")
        self.open_database()
        self.addCleanup(lambda: self.db.close())
        for migration in sorted((owner.BACKEND / "ai/migrations").glob("*.sql")):
            self.db.executescript(migration.read_text("utf-8"))

    def open_database(self):
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.create_function("unixepoch", 0, lambda: self.now)

    def run_sql(self, sql):
        return [dict(row) for row in self.db.execute(sql)]

    def policy(self):
        return self.run_sql("SELECT * FROM policy")[0]

    def ledger(self):
        return {
            table: self.run_sql("SELECT * FROM " + table + " ORDER BY rowid")
            for table in ("calls", "daily", "totals")
        }

    def prior_usage(self):
        self.db.execute("UPDATE policy SET paused=0")
        self.db.execute("INSERT INTO invites VALUES('fixture','person',9999999999)")
        self.db.execute("INSERT INTO devices VALUES('device','person','verifier','fixture',0)")
        for number, (charge, audio, state) in enumerate(
            [
                (1709500, 0, "held"),
                (4500000, 4, "held"),
                (93600, 0, "measured"),
                (90200, 0, "measured"),
                (300000, 4, "measured"),
            ]
        ):
            self.db.execute(
                """INSERT INTO calls(member,turn,step,device,day,model,seconds,is_plan,charge)
                VALUES('person',?,0,'device',date('now'),?,?,0,?)""",
                (str(number), "gpt-transcribe" if audio else "gpt-6-luna", audio, charge),
            )
            if state == "measured":
                self.db.execute("UPDATE calls SET state='measured' WHERE turn=?", (str(number),))
        self.db.execute("UPDATE members SET revoked=1")
        self.db.execute("UPDATE policy SET paused=1")

    def test_atomic_activation_preserves_existing_ledger_and_allowances_across_restart(self):
        self.prior_usage()
        before = self.ledger()
        old = self.policy()
        result = owner.activate_credit_beta(self.run_sql, self.expires)[0]
        self.assertEqual(result["funding"], "credit")
        self.assertEqual((result["seats"], result["paused"], result["credit_expires"]), (5, 0, self.expires))
        self.assertEqual(
            [result[k] for k in ("day_cap", "month_cap", "lifetime_cap")], [250000000, 1000000000, 1000000000]
        )
        self.assertEqual(
            [result[k] for k in ("turns", "planning", "rounds", "seconds")],
            [old[k] for k in ("turns", "planning", "rounds", "seconds")],
        )
        self.assertEqual(self.ledger(), before)
        self.db.close()
        self.open_database()
        self.assertEqual(self.policy(), result)
        self.assertEqual(self.ledger(), before)
        self.assertEqual(
            self.run_sql("SELECT amount FROM totals WHERE bucket='lifetime'")[0]["amount"], 6693300
        )

    def test_interrupted_command_has_only_paused_or_bounded_outcomes(self):
        old = self.policy()

        def lost_before(sql):
            raise ConnectionError("fixture failure before update")

        with self.assertRaises(ConnectionError):
            owner.activate_credit_beta(lost_before, self.expires)
        self.assertEqual(self.policy(), old)

        def lost_after(sql):
            self.run_sql(sql)
            raise ConnectionError("fixture lost reply after commit")

        with self.assertRaises(ConnectionError):
            owner.activate_credit_beta(lost_after, self.expires)
        current = self.policy()
        self.assertEqual((current["funding"], current["seats"], current["paused"]), ("credit", 5, 0))
        with self.assertRaises(SystemExit):
            owner.activate_credit_beta(self.run_sql, self.expires)
        self.assertEqual(self.policy(), current)
        with self.assertRaises(sqlite3.IntegrityError):
            self.run_sql("UPDATE policy SET seats=100")
        with self.assertRaises(sqlite3.IntegrityError):
            self.run_sql("UPDATE policy SET funding='normal',credit_expires=NULL")

    def test_expiry_blocks_database_admission_and_requires_paused_explicit_grant_update(self):
        owner.activate_credit_beta(self.run_sql, self.expires)
        self.run_sql("INSERT INTO invites VALUES('ticket','person',9999999999)")
        self.run_sql("INSERT INTO devices VALUES('device','person','verifier','ticket',0)")
        insert = """INSERT INTO calls(member,turn,step,device,day,model,seconds,is_plan,charge)
            VALUES('person','expired',0,'device',date('now'),'gpt-6-luna',0,0,1000)"""
        self.now = self.expires
        with self.assertRaisesRegex(sqlite3.IntegrityError, "service_paused"):
            self.run_sql(insert)
        self.run_sql("INSERT INTO invites VALUES('second','person',9999999999)")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "service_paused"):
            self.run_sql("INSERT INTO devices VALUES('device2','person','verifier2','second',0)")
        self.run_sql("UPDATE policy SET paused=1")
        with self.assertRaises(SystemExit):
            owner.activate_credit_beta(self.run_sql, self.expires)
        replacement = owner.grant_expiry("2026-12-01")
        owner.activate_credit_beta(self.run_sql, replacement)
        self.assertEqual(self.policy()["credit_expires"], replacement)
        self.run_sql(insert)

    def test_refuses_missing_guards_too_many_people_and_exhausted_buckets(self):
        self.run_sql(
            "INSERT INTO members VALUES('one',0),('two',0),('three',0),('four',0),('five',0),('six',0)"
        )
        with self.assertRaises(SystemExit):
            owner.activate_credit_beta(self.run_sql, self.expires)
        self.assertEqual(self.policy()["paused"], 1)
        self.run_sql("UPDATE members SET revoked=1")
        for bucket, cap in (
            ("'lifetime'", 1000000000),
            ("date('now')", 250000000),
            ("strftime('%Y-%m','now')", 1000000000),
        ):
            self.run_sql(
                f"INSERT INTO totals VALUES({bucket},{cap}) ON CONFLICT(bucket) DO UPDATE SET amount={cap}"
            )
            with self.assertRaises(SystemExit):
                owner.activate_credit_beta(self.run_sql, self.expires)
            self.run_sql(f"UPDATE totals SET amount=0 WHERE bucket={bucket}")
        self.run_sql("DROP TRIGGER credit_admission")
        with self.assertRaises(SystemExit):
            owner.activate_credit_beta(self.run_sql, self.expires)
        self.assertEqual(self.policy()["paused"], 1)

    def test_cli_attestations_are_distinct_and_normal_path_retains_capacity(self):
        with patch("sys.stderr", io.StringIO()):
            for args in (
                ["owner.py", "resume"],
                ["owner.py", "credit-beta", "--grant-expires", "2026-11-26"],
            ):
                with patch("sys.argv", args), self.assertRaises(SystemExit) as stopped:
                    owner.main()
                self.assertEqual(stopped.exception.code, 2)
        with (
            patch.object(owner, "execute", side_effect=lambda target, sql, persist=None: self.run_sql(sql)),
            patch("sys.stdout", io.StringIO()),
        ):
            with patch(
                "sys.argv", ["owner.py", "credit-beta", "--grant-expires", "2026-11-26", "--grant-reviewed"]
            ):
                owner.main()
            self.run_sql("UPDATE policy SET paused=1")
            with patch("sys.argv", ["owner.py", "resume", "--private-project-verified"]):
                owner.main()
            self.assertEqual(self.policy()["funding"], "normal")
            self.assertIsNone(self.policy()["credit_expires"])
            self.run_sql("UPDATE policy SET seats=100")
            self.assertEqual(self.policy()["seats"], 100)

    def test_reactivation_preserves_stricter_owner_limits(self):
        self.run_sql("UPDATE policy SET seats=3,day_cap=100000000,month_cap=500000000,lifetime_cap=500000000")
        owner.activate_credit_beta(self.run_sql, self.expires)
        self.assertEqual(
            [self.policy()[k] for k in ("seats", "day_cap", "month_cap", "lifetime_cap")],
            [3, 100000000, 500000000, 500000000],
        )
