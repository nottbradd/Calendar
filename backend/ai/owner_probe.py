"""A single, expiring, owner-operated trial. No OpenAI credentials or personal profiles on this path."""

import hashlib
import http.client
import json
import secrets
import subprocess
import sys
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from school_calendar import assistant_api as api
from school_calendar import assistant_hosted as hosted
from school_calendar.storage import Store
from scripts.verify_hosted import fictional_lookup, synthetic_wave

CAP = 50_000_000  # nano-USD; an absolute ceiling on each bucket, including prior spend/holds
LIMITS = {
    "seats": 1,
    "turns": 1,
    "planning": 0,
    "rounds": 2,
    "seconds": 5,
    "day_cap": CAP,
    "month_cap": CAP,
    "lifetime_cap": CAP,
}
# Stop before the observed grant's expiry date. Never infer a renewal from the service-key lifetime.
GRANT_END = int(datetime(2026, 11, 26, tzinfo=UTC).timestamp())


class Stopped(Exception):
    """A safe owner-facing reason; never contains a response body or credential."""


def check(condition, message):
    if not condition:
        raise Stopped(message)


def reservations():
    js = "import {probeReservations} from './ai/worker.js'; console.log(JSON.stringify(probeReservations()))"
    return json.loads(
        subprocess.check_output(
            ["node", "--input-type=module", "-e", js], cwd=ROOT / "backend", text=True, timeout=15
        )
    )


def snapshot(run):
    # One D1 read snapshot. All numbers are Worker accounting, never provider billing.
    (row,) = run("""SELECT p.*,datetime('now') AS utc,date('now') AS day,
      (SELECT COUNT(*) FROM members WHERE revoked=0) AS identities,
      (SELECT COUNT(*) FROM invites WHERE expires>unixepoch()) AS invitations,
      (SELECT COUNT(*) FROM owner_probe) AS probe_locks,
      (SELECT json_group_object(bucket,amount) FROM totals) AS totals,
      (SELECT COUNT(*) FROM calls) AS calls,
      (SELECT COUNT(*) FROM calls WHERE state='held') AS holds,
      (SELECT coalesce(sum(charge),0) FROM calls WHERE state='held') AS held_nusd,
      (SELECT coalesce(sum(input_tokens),0) FROM calls) AS input_tokens,
      (SELECT coalesce(sum(output_tokens),0) FROM calls) AS output_tokens,
      (SELECT coalesce(sum(audio_seconds),0) FROM calls) AS audio_seconds
      FROM policy p WHERE id=1""")
    policy = {key: row.pop(key) for key in ("id", "paused", *LIMITS)}
    return {**row, "policy": policy, "totals": json.loads(row["totals"])}


def headroom(before, bounds):
    total = sum(bounds.values())
    buckets = ("lifetime", before["day"][:7], before["day"])
    check("lifetime" in before["totals"] and total > 0, "Missing lifetime ledger or reservation bounds.")
    check(
        all(before["totals"].get(b, 0) + total <= CAP for b in buckets),
        "Existing usage/reservations leave insufficient room under the five-cent ceilings.",
    )
    check(
        all(before["policy"][k] >= CAP for k in ("day_cap", "month_cap", "lifetime_cap")),
        "The probe cannot raise an existing spending ceiling.",
    )
    return total


def stage(run, member, digest, required, remote):
    # Snapshot original policy, lock exclusively and apply limits atomically. Recheck room in D1.
    columns = ",".join(f"'{key}',{key}" for key in LIMITS)
    guards = (
        "enroll",
        "reserve",
        "reconcile",
        "probe_stage",
        "probe_invitation",
        "probe_open",
        "probe_policy",
        "probe_admission",
        "probe_close",
        "probe_before_restore",
        "probe_restore",
    )
    names = ",".join(f"'{name}'" for name in guards)
    expires = f"min(unixepoch()+600,{GRANT_END})" if remote else "unixepoch()+600"
    row = run(f"""INSERT INTO owner_probe(id,member,invite_hash,expires,previous_policy)
      SELECT 1,'{member}','{digest}',{expires},json_object({columns}) FROM policy WHERE id=1 AND paused=1
      AND {expires}>unixepoch() AND NOT EXISTS(SELECT 1 FROM owner_probe)
      AND (SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name IN ({names}))={len(guards)}
      AND min(day_cap,month_cap,lifetime_cap)>={CAP}
      AND EXISTS(SELECT 1 FROM totals WHERE bucket='lifetime')
      AND NOT EXISTS(SELECT 1 FROM totals WHERE bucket IN ('lifetime',date('now'),strftime('%Y-%m','now'))
        AND amount+{required}>{CAP}) RETURNING id""")
    check(row, "Could not stage: check the grant window, headroom, probe lock and installed D1 guards.")


def close_probe(run, member, verify=None):
    # The phase trigger pauses first, revokes this member and removes its unused invitation atomically.
    closed = run(f"UPDATE owner_probe SET phase='closing' WHERE member='{member}' RETURNING id")
    check(closed, "No matching owner probe to close.")
    try:
        return verify() if verify else None
    finally:
        check(
            run(f"DELETE FROM owner_probe WHERE member='{member}' AND phase='closing' RETURNING id"),
            "Could not restore the paused policy; run owner-probe-close.",
        )


class Worker:
    """Fixed live origin or explicit loopback mock. No redirects, proxy, retries or provider key."""

    def __init__(self, port=None):
        self.port = port
        self.bearer = uuid4().hex + "." + secrets.token_urlsafe(32)
        self.requests = {"chat": [], "transcribe": []}
        self.errors = {"chat": [], "transcribe": []}

    def request(self, route, body=None, headers=None):
        attempts = self.requests.get(route)
        if attempts is not None:
            check(len(attempts) < (2 if route == "chat" else 1), "Owner probe attempt limit reached.")
            attempts.append(None)  # An ambiguous transport failure consumes the attempt too.
            self.errors[route].append(None)
        conn = (
            http.client.HTTPConnection("127.0.0.1", self.port, timeout=40)
            if self.port
            else http.client.HTTPSConnection(hosted.HOST, timeout=40)
        )
        with closing(conn):
            if route == "health":
                conn.request("GET", "/health")
            else:
                binary = isinstance(body, bytes)
                conn.request(
                    "POST",
                    "/v1/" + route,
                    body if binary else json.dumps(body or {}).encode(),
                    {
                        "Content-Type": "application/octet-stream" if binary else "application/json",
                        "Authorization": "Bearer " + self.bearer,
                        **(headers or {}),
                    },
                )
            res = conn.getresponse()
            if attempts is not None:
                attempts[-1] = res.status
            if route == "health" and self.port:
                check(
                    res.getheader("X-Calendar-Probe-Mock") == "offline",
                    "Loopback is not the offline mock Worker.",
                )
            raw = res.read(600001)
            check(len(raw) <= 600000, "Oversized Worker reply.")
            data = json.loads(raw)
            check(isinstance(data, dict), "Malformed Worker reply.")
            if attempts is not None and res.status != 200:
                code = data.get("error")
                known = set(hosted.MESSAGES) | {"service_unavailable", "upstream_unavailable"}
                self.errors[route][-1] = code if isinstance(code, str) and code in known else "unrecognized"
            return res.status, data

    def ok(self, route, body):
        status, data = self.request(route, body)
        check(status == 200, f"Worker {route}: HTTP {status}.")
        return data


def endpoints(worker, store, pcm):
    """Two independent outcomes. Neither branch retries or uses a personal API transport."""
    turn = hosted.turn_id()
    today = datetime.now().astimezone()
    result = {}

    def respond(body):
        return worker.ok(
            "chat",
            {
                "turn": turn,
                "step": len(worker.requests["chat"]),
                "date": today.date().isoformat(),
                "zone": today.strftime("%z"),
                "input": body["input"],
            },
        )

    try:
        fictional_lookup(store, respond)
        result["lookup_passed"] = True
    except (Stopped, api.ApiError, OSError, ValueError, http.client.HTTPException):
        result["lookup_passed"] = False
    try:
        check(0 < len(pcm) <= 5 * 32000 and len(pcm) % 2 == 0, "Synthetic PCM outside probe limit.")
        status, data = worker.request("transcribe", pcm, {"X-Calendar-Request": hosted.turn_id()})
        result["transcription_passed"] = status == 200 and "tuesday" in str(data.get("text", "")).casefold()
    except (Stopped, OSError, ValueError, http.client.HTTPException):
        result["transcription_passed"] = False
    return {
        **result,
        "worker_http": worker.requests,
        "worker_error": worker.errors,
        "submitted_clip_seconds": len(pcm) / 32000,
    }


def reconcile(before, after, rows, requests):
    delta = Counter()
    for row in rows:
        for bucket in ("lifetime", row["day"][:7], row["day"]):
            delta[bucket] += row["charge"]
    keys = set(before["totals"]) | set(after["totals"]) | set(delta)
    return (
        all(after["totals"].get(k, 0) - before["totals"].get(k, 0) == delta[k] for k in keys)
        and len(rows) == after["calls"] - before["calls"]
        and sum(r["model"] == "gpt-6-luna" for r in rows) == len(requests["chat"])
        and sum(r["model"] == "gpt-transcribe" for r in rows) == len(requests["transcribe"])
        and all(
            after[k] - before[k] == sum(r[k] or 0 for r in rows)
            for k in ("input_tokens", "output_tokens", "audio_seconds")
        )
        and after["holds"] - before["holds"] == sum(r["state"] == "held" for r in rows)
        and after["held_nusd"] - before["held_nusd"] == sum(r["charge"] for r in rows if r["state"] == "held")
    )


def run_probe(run, target, port, receipt):
    remote = target == "remote"
    check(
        (not port if remote else port and 0 < port < 65536),
        "Local requires --mock-port; remote uses only the fixed Worker.",
    )
    worker = Worker(port)
    report = {
        "mode": "owner-only",
        "target": target,
        "provider_cap": "inactive",
        "provider_alerts": "paid_plan_required",
        "grant_expiry": "2026-11-26",
        "service_key_expiry": "2026-12-04",
        "posted_usd": None,
        "confirmed_grant_applied_usd": None,
    }

    def save():
        tmp = receipt.with_suffix(".tmp")
        tmp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        tmp.replace(receipt)

    receipt.parent.mkdir(parents=True, exist_ok=True)
    with receipt.open("x", encoding="utf-8") as f:
        f.write("{}\n")
    before = report["before"] = snapshot(run)
    bounds = report["max_reservations_nusd"] = reservations()
    save()
    check(
        not remote or datetime.now(UTC).timestamp() < GRANT_END,
        "Observed subscription grant window has ended.",
    )
    check(
        before["policy"]["paused"] == 1
        and not any(before[k] for k in ("identities", "invitations", "probe_locks")),
        "Start paused with zero active identities, live invitations and probe locks.",
    )
    check(
        worker.request("health") == (200, {"status": "ok", "paused": True}),
        "Worker did not confirm paused health.",
    )
    required = headroom(before, bounds)
    member, ticket = uuid4().hex, secrets.token_urlsafe(32)
    digest = hashlib.sha256(("invite:" + ticket).encode()).hexdigest()
    with (
        TemporaryDirectory(prefix="calendar-owner-fictional-") as folder,
        closing(Store(Path(folder) / "fictional.sqlite3")) as store,
    ):
        _, pcm = synthetic_wave(folder)  # Validate the real sample length before staging or enrollment.
        try:
            stage(run, member, digest, required, remote)
            staged = report["staged"] = snapshot(run)
            save()
            check(
                staged["policy"] == {"id": 1, "paused": 1, **LIMITS}
                and staged["totals"] == before["totals"]
                and staged["holds"] == before["holds"]
                and staged["identities"] == staged["invitations"] == 0,
                "Staged policy or baseline changed; refusing enrollment.",
            )
            headroom(staged, bounds)
            check(
                run(f"""INSERT INTO invites(hash,member,expires) SELECT invite_hash,member,expires FROM owner_probe
                      WHERE member='{member}' AND phase='staged' RETURNING member"""),
                "Invitation not issued.",
            )
            enrolled = worker.ok("enroll", {"invite": ticket})
            check(
                enrolled.get("member") == member and enrolled.get("device") == worker.bearer.split(".")[0],
                "Wrong enrollment.",
            )
            check(worker.ok("status", {}).get("paused") is True, "Enrollment did not remain paused.")
            check(
                run(
                    f"UPDATE owner_probe SET phase='running' WHERE member='{member}' AND phase='staged' RETURNING id"
                ),
                "Could not open isolated probe.",
            )
            active = report["active"] = snapshot(run)
            check(
                active["policy"] == {**staged["policy"], "paused": 0}
                and active["identities"] == active["probe_locks"] == 1
                and active["invitations"] == 0
                and active["totals"] == before["totals"],
                "Live probe failed its isolation check.",
            )
            report["endpoints"] = endpoints(worker, store, pcm)
        finally:
            # Also handle an ambiguous D1 response to stage: only clean up our own lock.
            if run(f"SELECT id FROM owner_probe WHERE member='{member}'"):

                def denied():
                    try:
                        status, data = worker.request("status")
                        return {"http": status, "revoked": status == 403 and data.get("error") == "revoked"}
                    except (Stopped, OSError, ValueError, http.client.HTTPException):
                        return {"http": None, "revoked": False}

                report["revocation"] = close_probe(run, member, denied)
            after = report["after"] = snapshot(run)
            rows = report["attempts"] = (
                run(f"""SELECT day,step,model,seconds,state,charge,input_tokens,output_tokens,
                    audio_seconds,upstream_http,upstream_code,upstream_type FROM calls WHERE member='{member}' ORDER BY rowid""")
            )
            report["reconciled"] = reconcile(before, after, rows, worker.requests)
            report["health_after"] = worker.request("health")[1]
            save()
    check(
        report["reconciled"]
        and after["policy"] == before["policy"]
        and not any(after[k] for k in ("identities", "invitations", "probe_locks"))
        and report["revocation"]["revoked"]
        and report["health_after"] == {"status": "ok", "paused": True},
        "Cleanup or ledger verification failed; inspect the receipt.",
    )
    return all(report["endpoints"][k] for k in ("lookup_passed", "transcription_passed"))
