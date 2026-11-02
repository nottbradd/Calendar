"""Exercise real encrypted Windows profiles against a disposable local or development D1 calendar."""
import argparse
from contextlib import ExitStack, closing
from dataclasses import replace
from datetime import date, time
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nacl.exceptions import CryptoError
from school_calendar import sync_crypto as crypto, sync_journal as journal
from school_calendar.model import Entry, SUBJECT_COLOURS
from school_calendar.storage import Store
from school_calendar.sync_client import Api, SERVICE, Sync, SyncError, summary


class MeasuredApi(Api):
    def __init__(self, base):
        super().__init__(base)
        self.opener.addheaders = [("X-Calender-Check", "disposable-v1")]
        self.interrupt = False

    def call(self, identity, action, body=None):
        response = super().call(identity, action, body)
        if action == "push" and self.interrupt:
            self.interrupt = False
            raise SyncError("connection")  # D1 committed, but the response never reached the client.
        return response


def denied(action, reasons=("denied",)):
    try:
        action()
    except SyncError as exc:
        assert exc.reason in reasons, exc.reason
    else:
        raise AssertionError("Expected the request to be rejected.")


def snapshot(store):
    state = store.read()
    return {e.id: journal.entry_value(e) for e in state.entries}, state.colours


def d1(target, sql):
    cmd = ["node", "node_modules/wrangler/bin/wrangler.js", "d1", "execute", "DB", target,
           "--command", sql, "--json"]
    result = subprocess.run(cmd, cwd=ROOT / "backend", capture_output=True, encoding="utf-8",
                            env={**os.environ, "WRANGLER_SEND_METRICS": "false", "CI": "true"})
    if result.returncode:
        raise RuntimeError("Wrangler could not verify the disposable calendar.")
    return json.loads(result.stdout)[0]["results"]


def pictures(store, folder):
    from PySide6.QtCore import QLocale
    from PySide6.QtWidgets import QApplication
    from school_calendar.appearance import apply_theme
    from school_calendar.sync_ui import ConflictDialog, SyncController, SyncDialog
    app = QApplication.instance() or QApplication([])
    QLocale.setDefault(QLocale("en_AU"))
    app.setQuitOnLastWindowClosed(False)
    apply_theme(app)
    folder.mkdir(parents=True, exist_ok=True)
    control = SyncController(None, store)
    control.timer.stop()
    settings = SyncDialog(None, control)
    conflict = ConflictDialog(settings, store, journal.conflicts(store.db)[0])
    for widget, filename in ((settings, "sync-settings.png"), (conflict, "sync-conflict.png")):
        widget.show()
        for _ in range(8):
            app.processEvents()
            sleep(.04)
        assert widget.grab().save(str(folder / filename))
    conflict.close()
    settings.close()
    control.close()
    app.processEvents()


def packaged_pair(exe, folder, a, b, entry):
    # Both real executables start at their normal entry point and read separate LocalAppData roots.
    a.store.save(entry)
    processes = []
    try:
        for name in ("A", "B"):
            env = dict(os.environ, LOCALAPPDATA=str(folder / name))
            env.pop("QT_QPA_PLATFORM", None)
            processes.append(subprocess.Popen([str(exe)], env=env, cwd=exe.parent))
            if name == "A":
                deadline = monotonic() + 35
                while summary(a.db)["pending"] and monotonic() < deadline:
                    sleep(.4)
                assert summary(a.db)["pending"] == 0, "Packaged A did not upload."
        deadline = monotonic() + 35
        while monotonic() < deadline:
            if any(p.poll() is not None for p in processes):
                raise RuntimeError("The packaged app exited unexpectedly.")
            if summary(a.db)["pending"] == 0 and any(e.id == entry.id for e in b.store.read().entries):
                print("Packaged Windows apps independently uploaded and downloaded the sample.")
                return
            sleep(.5)
        raise RuntimeError("The packaged profiles did not converge before the deadline.")
    finally:
        for p in processes:
            if p.poll() is None:
                subprocess.run(["taskkill.exe", "/PID", str(p.pid), "/T", "/F"], capture_output=True, check=False)
            p.wait(timeout=10)


def trial(args, folder, owned):
    api = MeasuredApi(args.base)
    target = "--remote" if args.base == SERVICE else "--local"
    with ExitStack() as stack:
        def profile(name):
            path = folder / name / "SchoolCalendar" / "calendar.sqlite3"
            return Sync(stack.enter_context(closing(Store(path))), api)
        a, b = profile("A"), profile("B")
        recovery = crypto.kit("CALR1")
        cal = crypto.parse_kit(recovery, "CALR1")[0]
        owned.append(cal)
        subject, note = "Sample Astronomy", "Disposable note about spectral lines"
        first = Entry("Spectra worksheet", date(2031, 4, 17), subject, at=time(10, 15), end_at=time(11, 0), notes=note)
        a.store.save(first)
        b_original = Entry("Sample local reading", date(2031, 4, 18))
        b.store.save(b_original)
        b.store.set_view("week")
        a.begin("create", recovery)
        a.run()
        _, owner = a.profile()
        wrong = {**owner, "token": crypto.random_key()}
        denied(lambda: api.call(wrong, "pull", {"after": 0}))
        # A legitimately authenticated member of another real calendar still has no access.
        other = profile("Other")
        other_kit = crypto.kit("CALR1")
        owned.append(crypto.parse_kit(other_kit, "CALR1")[0])
        other.begin("create", other_kit)
        other.run()
        _, outsider = other.profile()
        denied(lambda: api.call({**outsider, "calendar": cal}, "pull", {"after": 0}))
        expired = a.invite(seconds=1)
        waiting = profile("Expired")
        waiting.begin("join", expired)
        sleep(1.2)
        denied(waiting.run, ("invalid_invite",))
        ticket = a.invite()
        b.begin("join", ticket)
        assert b.run()["waiting"]
        _, candidate = b.profile()
        denied(lambda: api.call(candidate, "pull", {"after": 0}))
        thief = profile("Replay")
        thief.begin("join", ticket)
        denied(thief.run, ("invalid_invite",))
        try:
            a.approve("000-invalid")
        except ValueError:
            pass
        else:
            raise AssertionError("An incorrect pairing comparison was accepted.")
        a.approve(crypto.fingerprint(candidate["device"], candidate["pub"]))
        b.run()
        denied(thief.run, ("invalid_invite",))
        a.run()
        assert snapshot(a.store) == snapshot(b.store)
        assert a.store.read().view == "month" and b.store.read().view == "week"
        print("Pair approval, expiry, replay, cross-calendar denial and existing-data merge passed.")
        item = next(e for e in b.store.read().entries if e.id == first.id)
        b.store.save(replace(item, subject="Sample Optics", notes="Revised disposable notes",
                             completed=True, at=time(13, 5), end_at=time(14, 20)))
        b.store.set_subject_colour("Sample Optics", SUBJECT_COLOURS[5])
        b.run()
        a.run()
        assert snapshot(a.store) == snapshot(b.store)
        assert a.store.read().colours["Sample Optics"] == SUBJECT_COLOURS[5]
        b.store.remove_subject("Sample Optics")
        b.run()
        a.run()
        assert "Sample Optics" not in a.store.read().colours
        assert next(e for e in a.store.read().entries if e.id == first.id).subject == ""
        a.store.add_subject("Sample Optics", SUBJECT_COLOURS[5])
        a.run()
        b.run()
        assert next(e for e in b.store.read().entries if e.id == first.id).subject == "Sample Optics"
        offline_a = Entry("Lab equipment", date(2031, 4, 20))
        offline_b = Entry("Practice questions", date(2031, 4, 21))
        a.store.save(offline_a)
        b.store.save(offline_b)
        api.interrupt = True
        denied(a.run, ("connection",))
        assert summary(a.db)["pending"] > 0
        a.store.close()
        a = profile("A")
        a.run()
        b.run()
        a.run()
        assert snapshot(a.store) == snapshot(b.store)
        assert {offline_a.id, offline_b.id} <= set(snapshot(a.store)[0])
        # A delete made while the other PC is offline preserves its competing edit.
        target_a = next(e for e in a.store.read().entries if e.id == offline_a.id)
        target_b = next(e for e in b.store.read().entries if e.id == offline_a.id)
        a.store.delete(target_a)
        b.store.save(replace(target_b, notes="Edited offline"))
        a.run()
        b.run()
        a.run()
        entity = "e:" + offline_a.id
        assert entity in journal.conflicts(a.db) and offline_a.id not in snapshot(a.store)[0]
        versions = journal.heads(a.db, entity)
        deletion = next(r["op"] for r in versions if r["payload"] == "null")
        journal.resolve(a.db, entity, [r["op"] for r in versions], deletion)
        a.run()
        b.run()
        assert not journal.conflicts(b.db) and offline_a.id not in snapshot(b.store)[0]
        # Two genuine edits, both retained until an explicit resolution.
        one = next(e for e in a.store.read().entries if e.id == first.id)
        two = next(e for e in b.store.read().entries if e.id == first.id)
        a.store.save(replace(one, title="Spectra worksheet · lab session", notes="Bring the spectrometer."))
        b.store.save(replace(two, title="Spectra worksheet · study group", notes="Meet in the library."))
        a.run()
        b.run()
        a.run()
        entity = "e:" + first.id
        assert journal.conflicts(a.db) == journal.conflicts(b.db) == [entity]
        if args.screenshots:
            pictures(a.store, args.screenshots)
        versions = journal.heads(a.db, entity)
        assert len(versions) == 2
        chosen = next(r["op"] for r in versions if "study group" in r["payload"])
        journal.resolve(a.db, entity, [r["op"] for r in versions], chosen)
        a.run()
        b.run()
        assert not journal.conflicts(b.db) and snapshot(a.store) == snapshot(b.store)
        print("Timetables, colours, notes, completion, offline edits, lost reply, restart and conflict choices passed.")
        if args.packaged:
            packaged_pair(args.packaged.resolve(), folder, a, b, Entry("Packaged sample", date(2031, 4, 22)))
        survivor = profile("Survivor")
        survivor.begin("recover", recovery)
        survivor.run()
        _, old = b.profile()
        a.revoke(old["device"])
        new_entry = Entry("Rotation sample", date(2031, 4, 23))
        a.store.save(new_entry)
        a.run()
        denied(b.run)
        denied(lambda: api.call(old, "push", {"epoch": 1, "changes": []}))
        # A previously claimed invite cannot undo the revocation.
        denied(thief.run, ("invalid_invite",))
        survivor.run()
        recovered = profile("Replacement")
        recovered.begin("recover", recovery)
        recovered.run()
        assert snapshot(recovered.store) == snapshot(a.store) == snapshot(survivor.store)
        _, active = a.profile()
        feed = []
        while True:
            page = api.call(active, "pull", {"after": feed[-1]["seq"] if feed else 0})
            feed.extend(page["changes"])
            if not page["more"]:
                break
        assert len({r["op"] for r in feed}) == len(feed)
        latest = next(r for r in feed if r["epoch"] == 2)
        try:
            crypto.decrypt(cal, {"2": old["keys"]["1"]}, latest)
        except CryptoError:
            pass
        else:
            raise AssertionError("The revoked key opened new ciphertext.")
        replay = profile("Reverse")
        with replay.db:
            for change in [*reversed(feed), *feed]:
                journal.receive(replay.db, change["op"], crypto.decrypt(cal, active["keys"], change))
        assert snapshot(replay.store) == snapshot(a.store)
        assert replay.db.execute("SELECT count(*) FROM sync_ops WHERE pending=1").fetchone()[0] == 0
        a.run()  # Observe the replacement PC joining before measuring an unchanged calendar.
        before = api.requests
        a.run()
        idle = api.requests - before
        assert idle == 1
        retained = snapshot(b.store)
        b.disconnect()
        assert snapshot(b.store) == retained and summary(b.db)["phase"] == "disabled"
        if args.audit:
            rows = d1(target, f"SELECT box FROM sync_changes WHERE calendar='{cal}'")
            directory = d1(target, f"SELECT directory FROM sync_calendars WHERE id='{cal}'")
            stored = json.dumps([rows, directory])
            sensitive = [first.title, subject, note, "2031-04-17", "Revised disposable notes", "Sample Optics",
                         owner["token"], old["token"], active["private"], *active["keys"].values(),
                         recovery.split(".")[-1], ticket.split(".")[2]]
            assert rows and not any(s in stored for s in sensitive)
            print("D1 payloads and directory inspected: no sample content, device bearer, recovery seed or content key.")
        lost = profile("AllLost")
        lost.begin("recover", recovery, reset=True)
        lost.run()
        assert snapshot(lost.store) == snapshot(a.store) and len(lost.profile()[1]["state"]["members"]) == 1
        denied(a.run)
        denied(survivor.run)
        print("Revocation, rotation, survivor rekey, lost-PC recovery, reverse delivery and local disconnect passed.")
        print(json.dumps({"requests": api.requests, "json_upload_bytes": api.sent_bytes,
                          "json_download_bytes": api.received_bytes, "stored_operations": len(feed),
                          "idle_requests": idle, "profiles": "isolated Windows profiles on one PC"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", choices=[SERVICE, "http://127.0.0.1:18787"], default="http://127.0.0.1:18787")
    parser.add_argument("--audit", action="store_true", help="Inspect, then remove only calendars generated in this run with Wrangler.")
    parser.add_argument("--screenshots", type=Path)
    parser.add_argument("--packaged", type=Path, help="Also run two isolated copies of a development Windows executable.")
    args = parser.parse_args()
    if args.packaged and args.base != SERVICE:
        parser.error("The packaged desktop app uses the deployed development endpoint.")
    owned = []
    try:
        with TemporaryDirectory(prefix="calender-sync-trial-") as tmp:
            trial(args, Path(tmp), owned)
    finally:
        if args.audit:
            target = "--remote" if args.base == SERVICE else "--local"
            for cal in owned:
                d1(target, f"DELETE FROM sync_calendars WHERE id='{cal}'")
                left = d1(target, f"SELECT (SELECT count(*) FROM sync_changes WHERE calendar='{cal}') + "
                                  f"(SELECT count(*) FROM sync_calendars WHERE id='{cal}') AS leftover")
                assert left[0]["leftover"] == 0, "Disposable calendar cleanup was incomplete."
