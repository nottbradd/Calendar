"""Settings and background scheduling for the opt-in Windows sync prototype."""
import json
import random
import threading
from contextlib import closing
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QInputDialog, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit,
)

from . import sync_crypto as crypto
from . import sync_journal as journal
from .appearance import button, label
from .dialogs import PlannerDialog
from .storage import Store
from .sync_client import Api, Sync, SyncError, summary


class SyncController(QObject):
    changed = Signal()
    completed = Signal(str, object)
    delivered = Signal(str, object, str, str)

    def __init__(self, parent, store, api_factory=Api):
        super().__init__(parent)
        self.store, self.api_factory = store, api_factory
        self.busy = self.closed = False
        self.error = self.error_code = ""
        self.uploaded = self.failures = 0
        self.timer = QTimer(self, singleShot=True)
        self.timer.timeout.connect(lambda: self.start("run"))
        self.delivered.connect(self._done)
        if summary(store.db)["phase"] != "disabled":
            self.timer.start(1200)

    def local_change(self):
        info = summary(self.store.db)
        if info["pending"] > info["oversized"] and not self.busy and not self.error and info["phase"] == "active":
            if not self.timer.isActive() or self.timer.remainingTime() > 3000:
                self.timer.start(2500)
        self.changed.emit()

    def status(self, info):
        if self.busy:
            return "Checking for changes…"
        if self.error:
            return self.error
        if info["phase"] == "disabled":
            return "Sync disabled"
        if info["phase"] == "join":
            return "Waiting for approval from a paired PC"
        if info["phase"] != "active":
            return "Waiting to finish linking this PC"
        if info["conflicts"]:
            return "Conflict needs attention"
        if info["oversized"]:
            return "Large item saved locally. Shorten its text to sync it."
        if info["pending"]:
            return "Saved locally, waiting to upload"
        if self.uploaded:
            return "Changes uploaded"
        return "Up to date" if info["contact"] else "Ready to check for changes"

    def start(self, action, *args):
        if self.busy or self.closed:
            return
        if action == "run" and summary(self.store.db)["phase"] == "disabled":
            return
        self.busy = True
        self.error = self.error_code = ""
        self.timer.stop()
        self.changed.emit()
        path = self.store.path

        def work():
            result, error, code = None, "", ""
            try:
                with closing(Store(path)) as store:
                    engine = Sync(store, self.api_factory())
                    if action in ("create", "join", "recover"):
                        engine.begin(action, *args)
                        result = engine.run()
                    else:
                        result = getattr(engine, action)(*args)
            except SyncError as exc:
                error, code = str(exc), exc.reason
            except ValueError as exc:
                # Do not display decoder errors containing decrypted input.
                safe = ("That code", "Disconnect the current", "The verification code", "Resolve competing",
                        "Windows could not", "The calendar key envelope")
                error = str(exc) if str(exc).startswith(safe) else "A sync record could not be verified. Your local data is safe."
            except Exception:
                error = "Sync could not complete. Your local data is safe. Try Sync now."
            if not self.closed:
                self.delivered.emit(action, result, error, code)

        threading.Thread(target=work, name="Calender sync", daemon=True).start()

    def _done(self, action, result, error, code):
        if self.closed:
            return
        self.busy, self.error, self.error_code = False, error, code
        self.uploaded = result.get("uploaded", 0) if isinstance(result, dict) else 0
        self.failures = min(self.failures + 1, 7) if error else 0
        info = summary(self.store.db)
        if info["phase"] != "disabled" and code not in ("denied", "invalid_invite", "too_large"):
            interval = min(900, 15 * 2 ** (self.failures - 1)) if error else (12 if info["phase"] == "join" else 90)
            if code == "slow_down":
                interval = max(interval, 65)
            self.timer.start(int((interval + random.uniform(0, min(10, interval / 5))) * 1000))
        self.completed.emit(action, result)
        self.changed.emit()

    def close(self):
        self.closed = True
        self.timer.stop()


def note(text):
    out = label(text, "muted")
    out.setWordWrap(True)
    return out


def describe(entity, value):
    if value is None:
        return "Deleted on one device"
    if entity.startswith("s:"):
        return value["name"] + "  ·  " + value["colour"]
    entry = journal.decode_entry(entity[2:], value)
    return "\n".join((entry.title, f"{entry.day:%d %b %Y}  ·  {entry.time_label}",
                      f"{entry.subject or 'No subject'}  ·  {entry.type_label}  ·  " +
                      ("Completed" if entry.completed else "Open"), entry.notes or "No notes"))


class ConflictDialog(PlannerDialog):
    def __init__(self, parent, store, entity):
        super().__init__(parent, "Resolve conflict")
        self.store, self.entity = store, entity
        self.body.addWidget(label("Choose the version to keep", "heading"))
        self.body.addWidget(note("These changes were made independently. Compare each version before resolving."))
        self.versions = journal.heads(store.db, entity)
        self.options = QListWidget()
        self.options.setObjectName("syncList")
        self.options.viewport().setAutoFillBackground(False)
        self.options.setMaximumHeight(110)
        self.options.setAccessibleName("Competing versions")
        self.body.addWidget(self.options)
        self.body.addWidget(label("Selected version", "field"))
        self.preview = QPlainTextEdit()
        self.preview.setObjectName("detailNotes")
        self.preview.setMinimumHeight(108)
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("Selected version details")
        self.body.addWidget(self.preview, 1)
        for idx, row in enumerate(self.versions, 1):
            text = describe(entity, json.loads(row["payload"])).splitlines()
            self.options.addItem(f"Version {idx}   ·   " + "   ·   ".join(text[:2]))
        self.options.currentRowChanged.connect(self.show_version)
        self.options.setCurrentRow(0)
        self.error = self.error_label()
        self.body.addWidget(self.error)
        actions = QHBoxLayout()
        actions.addStretch()
        later = button("Decide later")
        later.clicked.connect(self.reject)
        actions.addWidget(later)
        self.choose = button("Use selected version", "primary")
        self.choose.clicked.connect(self.resolve)
        actions.addWidget(self.choose)
        self.body.addLayout(actions)
        self.resize(540, 395)

    def show_version(self, index):
        if index >= 0:
            self.preview.setPlainText(describe(self.entity, json.loads(self.versions[index]["payload"])))

    def resolve(self):
        try:
            row = self.versions[self.options.currentRow()]
            journal.resolve(self.store.db, self.entity, [v["op"] for v in self.versions], row["op"])
        except (ValueError, IndexError) as exc:
            self.error.setText(str(exc))
            self.error.show()
            return
        self.accept()


class SyncDialog(PlannerDialog):
    def __init__(self, parent, controller):
        super().__init__(parent, "Sync settings")
        self.control = controller
        self.store = controller.store
        self.body.addWidget(label("Calendar sync", "heading"))
        self.status_label = label("", "field")
        self.status_label.setWordWrap(True)
        self.body.addWidget(self.status_label)
        self.contact = note("")
        self.body.addWidget(self.contact)
        self.message = note("Linking merges entries and subjects already on both PCs. "
                            "Matching IDs with different contents are kept for review. Views stay on each PC.")
        self.body.addWidget(self.message)
        self.setup_row = self.actions([("Create calendar", self.create), ("Join calendar", self.join), ("Recover", self.recover)])
        self.devices_title = label("Paired devices", "field")
        self.body.addWidget(self.devices_title)
        self.devices = QListWidget()
        self.devices.setObjectName("syncList")
        self.devices.viewport().setAutoFillBackground(False)
        self.devices.setAccessibleName("Paired devices")
        self.devices.setMaximumHeight(104)
        self.devices.currentRowChanged.connect(self.selection)
        self.body.addWidget(self.devices)
        self.member_ids = []
        self.manage = self.actions([("Invite device", lambda: self.control.start("invite")),
                                    ("Approve request", self.approve), ("Revoke device", self.revoke)])
        self.code = QLineEdit()
        self.code.setReadOnly(True)
        self.code.setAccessibleName("Invitation or verification code")
        self.code.hide()
        self.body.addWidget(self.code)
        self.conflict_button = button("Review conflicts")
        self.conflict_button.clicked.connect(self.review)
        self.body.addWidget(self.conflict_button)
        self.body.addStretch()
        self.footer = self.actions([("Disconnect", self.disconnect), ("Done", self.accept),
                                    ("Sync now", lambda: self.control.start("run"))])
        self.control.changed.connect(self.refresh)
        self.control.completed.connect(self.completed)
        self.finished.connect(self.unbind)
        self.resize(510, 370)
        self.refresh()

    def actions(self, items):
        row = QHBoxLayout()
        controls = []
        for title, action in items:
            control = button(title, "primary" if title in ("Create calendar", "Sync now") else "")
            control.clicked.connect(action)
            row.addWidget(control)
            controls.append(control)
        self.body.addLayout(row)
        return controls

    def unbind(self):
        self.control.changed.disconnect(self.refresh)
        self.control.completed.disconnect(self.completed)
        self.code.clear()

    def refresh(self):
        info = summary(self.store.db)
        phase, busy = info["phase"], self.control.busy
        self.status_label.setText(self.control.status(info))
        when = datetime.fromisoformat(info["contact"]).astimezone().strftime("%d %b, %H:%M:%S") if info["contact"] else "No successful sync yet"
        self.contact.setText(f"Last server contact  ·  {when}\nWaiting to upload  ·  {info['pending']}")
        for control in self.setup_row:
            control.setVisible(phase == "disabled")
            control.setEnabled(not busy)
        self.message.setVisible(phase != "active")
        self.devices_title.setVisible(phase == "active")
        self.devices.setVisible(phase == "active")
        for control in self.manage:
            control.setVisible(phase == "active")
            control.setEnabled(not busy)
        self.footer[0].setVisible(phase != "disabled")
        self.footer[0].setEnabled(not busy)
        self.footer[2].setVisible(phase != "disabled")
        self.footer[2].setEnabled(not busy)
        self.conflict_button.setVisible(bool(info["conflicts"]))
        self.conflict_button.setText(f"Review conflicts ({len(info['conflicts'])})")
        self.conflicts = info["conflicts"]
        if phase == "disabled":
            self.code.clear()
            self.code.hide()
            return
        try:
            _, ident = Sync(self.store).profile()
        except ValueError:
            self.status_label.setText("Windows could not unlock this profile. Local entries are available.")
            return
        if phase == "join":
            self.code.setText(crypto.fingerprint(ident["device"], ident["pub"]))
            self.code.show()
            self.message.setText("Read this verification code to the person approving this PC.")
        elif phase == "active":
            if self.code.isVisible() and not self.code.text().startswith("CALI1."):
                self.code.hide()
            state = ident["state"]
            self.self_id = ident["device"]
            selected = self.devices.currentRow()
            self.member_ids = [m["id"] for m in state["members"]]
            self.devices.blockSignals(True)
            self.devices.clear()
            for device in self.member_ids:
                self.devices.addItem(("This PC" if device == self.self_id else "Paired PC") + "   ·   " + device[:12])
            self.devices.setCurrentRow(selected if 0 <= selected < len(self.member_ids) else 0)
            self.devices.blockSignals(False)
            self.manage[1].setEnabled(bool(state["pending"]) and not busy)
            self.selection()

    def selection(self):
        index = self.devices.currentRow()
        if hasattr(self, "manage") and hasattr(self, "self_id"):
            self.manage[2].setEnabled(not self.control.busy and 0 <= index < len(self.member_ids) and
                                      self.member_ids[index] != self.self_id)

    def completed(self, action, result):
        if action == "invite" and isinstance(result, str):
            self.code.setText(result)
            self.code.setToolTip("Share privately. Expires in 10 minutes and can be claimed once.")
            self.code.show()
            self.code.selectAll()
        if action in ("approve", "revoke", "disconnect") and not self.control.error:
            self.code.clear()
            self.code.hide()

    def create(self):
        kit = crypto.kit("CALR1")
        dialog = PlannerDialog(self, "Save recovery key")
        dialog.body.addWidget(label("Save your recovery key", "heading"))
        dialog.body.addWidget(note("Store this private key offline. Anyone with it can recover the calendar. "
                                   "If every paired PC and this key are lost, the encrypted calendar cannot be recovered."))
        field = QLineEdit(kit)
        field.setReadOnly(True)
        field.setAccessibleName("Recovery key")
        dialog.body.addWidget(field)
        check = QCheckBox("I saved the recovery key in a safe place")
        dialog.body.addWidget(check)
        go = button("Create synced calendar", "primary")
        go.setEnabled(False)
        check.toggled.connect(go.setEnabled)
        go.clicked.connect(dialog.accept)
        dialog.body.addWidget(go)
        dialog.resize(490, 220)
        if dialog.exec():
            self.control.start("create", kit)

    def join(self):
        value, ok = QInputDialog.getText(self, "Join calendar", "Private invitation from a paired PC")
        if ok:
            self.control.start("join", value)

    def recover(self):
        dialog = PlannerDialog(self, "Recover calendar")
        dialog.body.addWidget(label("Recover on this PC", "heading"))
        dialog.body.addWidget(note("Enter the private recovery key you saved when you created the calendar. "
                                   "Your local entries will merge with the recovered calendar."))
        value = QLineEdit()
        value.setEchoMode(QLineEdit.EchoMode.Password)
        value.setAccessibleName("Private recovery key")
        dialog.body.addWidget(value)
        reset = QCheckBox("I lost all paired PCs. Revoke them and rotate the key.")
        dialog.body.addWidget(reset)
        go = button("Recover calendar", "primary")
        go.clicked.connect(dialog.accept)
        dialog.body.addWidget(go)
        dialog.resize(480, 220)
        if dialog.exec():
            self.control.start("recover", value.text(), reset.isChecked())

    def approve(self):
        value, ok = QInputDialog.getText(self, "Approve device", "Enter the verification code shown on the joining PC")
        if ok:
            self.control.start("approve", value)

    def revoke(self):
        device = self.member_ids[self.devices.currentRow()]
        if QMessageBox.question(self, "Revoke device", f"Revoke PC {device[:12]} and rotate the calendar key? "
                                "Copies already on that PC remain readable.",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.control.start("revoke", device)

    def disconnect(self):
        if QMessageBox.question(self, "Disconnect this PC", "Keep local entries and forget this PC’s sync keys and queue? "
                                "Pending changes will stay on this PC. Revoke its device ID from another PC to remove server access.",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.control.start("disconnect")

    def review(self):
        if self.conflicts:
            dialog = ConflictDialog(self, self.store, self.conflicts[0])
            if dialog.exec():
                self.control.local_change()
