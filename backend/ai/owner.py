"""Manage Calendar AI through the owner's Wrangler session. No public admin endpoints."""

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import time
from datetime import UTC, date, datetime
from decimal import Decimal, DecimalException
from pathlib import Path
from uuid import uuid4

BACKEND = Path(__file__).resolve().parents[1]
FIELDS = {"seats", "turns", "planning", "rounds", "seconds", "day_cap", "month_cap", "lifetime_cap"}


def grant_expiry(text):
    try:
        day = date.fromisoformat(text)
        if day.isoformat() != text:
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError("Use the checked grant expiry date as YYYY-MM-DD.") from None
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())


def activate_credit_beta(run, expires):
    # One statement. A lost reply can leave only the old paused policy or this bounded policy.
    rows = run(f"""UPDATE policy SET funding='credit',credit_expires={int(expires)},
        seats=min(seats,5),day_cap=min(day_cap,250000000),
        month_cap=min(month_cap,1000000000),lifetime_cap=min(lifetime_cap,1000000000),paused=0
      WHERE id=1 AND paused=1 AND {int(expires)}>unixepoch()
        AND NOT EXISTS(SELECT 1 FROM owner_probe)
        AND (SELECT count(*) FROM members WHERE revoked=0)<=min(seats,5)
        AND EXISTS(SELECT 1 FROM totals WHERE bucket='lifetime')
        AND NOT EXISTS(SELECT 1 FROM totals WHERE
          (bucket='lifetime' AND amount>=min(lifetime_cap,1000000000)) OR
          (bucket=strftime('%Y-%m','now') AND amount>=min(month_cap,1000000000)) OR
          (bucket=date('now') AND amount>=min(day_cap,250000000)))
        AND (SELECT count(*) FROM sqlite_master WHERE type='trigger'
          AND name IN ('enroll','reserve','reconcile','credit_configuration','credit_admission','credit_enrollment'))=6
      RETURNING *""")
    if not rows:
        raise SystemExit(
            "Credit beta was not activated. Check pause state, grant expiry, active identities, "
            "remaining budget, probe locks and migrations. Accounting is retained."
        )
    return rows


def identifier(text):
    if not re.fullmatch("[0-9a-f]{32}", text):
        raise argparse.ArgumentTypeError("Expected a 32-character lowercase identity or device ID.")
    return text


def execute(target, sql, persist=None):
    args = [
        "node",
        "node_modules/wrangler/bin/wrangler.js",
        "d1",
        "execute",
        "AI_DB",
        "--config",
        "ai/wrangler.jsonc",
        "--" + target,
        "--command",
        sql,
        "--json",
    ]
    if persist:
        if target != "local":
            raise SystemExit("--persist-to is for isolated local D1 only.")
        args.extend(["--persist-to", str(persist)])
    result = subprocess.run(
        args,
        cwd=BACKEND,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=50,
        env={
            **{key: os.environ[key] for key in os.environ if not key.upper().startswith("OPENAI_")},
            "CI": "true",
            "WRANGLER_SEND_METRICS": "false",
        },
    )
    if result.returncode:
        raise SystemExit("D1 operation failed. Check Wrangler access, migrations and allowed policy values.")
    return json.loads(result.stdout)[0]["results"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("local", "remote"), default="local")
    parser.add_argument(
        "--persist-to", type=Path, help="Isolated local Wrangler state for an offline rehearsal."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    invite = commands.add_parser("invite", help="Issue privately after verifying the recipient.")
    whom = invite.add_mutually_exclusive_group(required=True)
    whom.add_argument("--new-person", action="store_true")
    whom.add_argument("--member", type=identifier)
    invite.add_argument("--minutes", type=int, choices=range(1, 61), default=10, metavar="1..60")
    for name in ("revoke-user", "revoke-device", "devices"):
        commands.add_parser(name).add_argument("id", type=identifier)
    commands.add_parser("pause")
    commands.add_parser("usage")
    commands.add_parser("members")
    credit = commands.add_parser(
        "credit-beta",
        help="Enable the five-person credit-funded beta while provider safeguards are unavailable.",
    )
    credit.add_argument(
        "--grant-expires",
        type=grant_expiry,
        required=True,
        metavar="YYYY-MM-DD",
        help="Reject new sponsored requests at 00:00 UTC on this verified grant expiry date.",
    )
    credit.add_argument(
        "--grant-reviewed",
        action="store_true",
        required=True,
        help="Confirm the grant window was checked. Provider cap is inactive and alerts are unavailable.",
    )
    probe = commands.add_parser(
        "owner-probe", help="Owner only: one fictional trial; provider cap/alerts unavailable."
    )
    probe.add_argument("--mock-port", type=int, help="Local only: port of the offline fixture Worker.")
    commands.add_parser(
        "owner-probe-close", help="Pause, revoke and restore from an interrupted owner's D1 snapshot."
    )
    commands.add_parser("resume").add_argument(
        "--private-project-verified",
        action="store_true",
        required=True,
        help="Confirm dedicated restricted service key, sharing disabled, enforced provider spend limit and working alerts.",
    )
    limits = commands.add_parser("limit")
    limits.add_argument("field", choices=sorted(FIELDS))
    limits.add_argument("value", help="Integer units, or USD for fields ending in _cap.")
    args = parser.parse_args()

    def run(sql):
        return execute(args.target, sql, args.persist_to.resolve() if args.persist_to else None)

    if args.command == "credit-beta":
        rows = activate_credit_beta(run, args.grant_expires)
        print("Credit-funded beta active. Provider cap inactive; provider alerts unavailable.")
        print("Grant cutoff:", datetime.fromtimestamp(args.grant_expires, UTC).isoformat())
        print(json.dumps(rows, indent=2))
    elif args.command in ("owner-probe", "owner-probe-close"):
        from owner_probe import Stopped, close_probe, run_probe

        try:
            if args.command == "owner-probe-close":
                lock = run("SELECT member FROM owner_probe WHERE id=1")
                if not lock:
                    raise SystemExit("No interrupted owner probe found; no policy changed.")
                close_probe(run, identifier(lock[0]["member"]))
                print(
                    "Paused, revoked the disposable identity, and restored the prior policy. Ledger retained."
                )
                return
            receipt = BACKEND.parent / "artifacts" / f"owner-probe-{args.target}-{uuid4().hex}.json"
            print(
                "Owner-only probe. Provider cap and alerts unavailable. Verification record:",
                receipt,
                flush=True,
            )
            if not run_probe(run, args.target, args.mock_port, receipt):
                raise SystemExit(
                    "Probe finished with an endpoint failure. Inspect the separate outcomes in the record."
                )
            print("Both endpoints passed. Cleanup and ledger verified.")
        except Stopped as exc:
            raise SystemExit(str(exc)) from None
    elif args.command == "invite":
        member = uuid4().hex if args.new_person else args.member
        if not args.new_person and not run(
            f"SELECT id FROM members WHERE id='{member}' AND revoked=0 UNION "
            f"SELECT member FROM invites WHERE member='{member}' "
            f"AND NOT EXISTS(SELECT 1 FROM members WHERE id='{member}' AND revoked=1)"
        ):
            raise SystemExit("Unknown or revoked identity. Verify the person's original ID.")
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(("invite:" + token).encode()).hexdigest()
        expiry = int(time.time()) + 60 * args.minutes
        run(f"INSERT INTO invites(hash,member,expires) VALUES ('{digest}','{member}',{expiry})")
        print(
            "Private invitation for",
            member,
            "expires",
            datetime.fromtimestamp(expiry, UTC).isoformat(),
        )
        print("CAI1." + token)
    elif args.command == "usage":
        print(
            json.dumps(
                {
                    "policy": run("""SELECT *,datetime(credit_expires,'unixepoch') AS credit_expires_utc,
                        paused OR (funding='credit' AND credit_expires<=unixepoch()) AS effective_paused FROM policy"""),
                    "totals_nano_usd": run("SELECT * FROM totals ORDER BY bucket"),
                    "today": run(
                        "SELECT count(*) AS active_people,coalesce(sum(turns),0) AS turns,coalesce(sum(planning),0) AS planning,coalesce(sum(seconds),0) AS seconds FROM daily WHERE day=date('now')"
                    ),
                    "recent_requests": run(
                        "SELECT model,state,count(*) AS requests,sum(charge) AS estimated_nano_usd FROM calls GROUP BY model,state"
                    ),
                },
                indent=2,
            )
        )
    elif args.command == "members":
        print(json.dumps(run("SELECT id,revoked FROM members ORDER BY id"), indent=2))
    elif args.command == "devices":
        print(json.dumps(run(f"SELECT id,revoked FROM devices WHERE member='{args.id}'"), indent=2))
    elif args.command == "revoke-user":
        run(f"INSERT INTO members(id,revoked) VALUES ('{args.id}',1) ON CONFLICT(id) DO UPDATE SET revoked=1")
    elif args.command == "revoke-device":
        if not run(f"UPDATE devices SET revoked=1 WHERE id='{args.id}' RETURNING id"):
            raise SystemExit("No such device.")
    elif args.command in ("pause", "resume"):
        if args.command == "pause":
            run("UPDATE policy SET paused=1 WHERE id=1")
        elif not run("""UPDATE policy SET paused=0,funding='normal',credit_expires=NULL WHERE id=1
                AND (funding='normal' OR paused=1) AND NOT EXISTS(SELECT 1 FROM owner_probe) RETURNING id"""):
            raise SystemExit("Pause the service and close any owner probe before normal activation.")
    elif args.command == "limit":
        try:
            amount = Decimal(args.value) * (1000000000 if args.field.endswith("_cap") else 1)
        except DecimalException:
            raise SystemExit("Enter a number for the policy value.") from None
        if not amount.is_finite() or amount != amount.to_integral_value() or not 0 <= amount <= 10**12:
            raise SystemExit("Invalid policy value.")
        run(f"UPDATE policy SET {args.field}={int(amount)} WHERE id=1")
    if args.command in ("pause", "resume", "limit"):
        print(json.dumps(run("SELECT * FROM policy"), indent=2))


if __name__ == "__main__":
    main()
