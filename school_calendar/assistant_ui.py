"""Session-only assistant drawer. Only the local Apply button can change the database."""

from contextlib import closing
import io
import threading
from time import monotonic
import wave

from PySide6.QtCore import Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import assistant_api as api, assistant_config as config, assistant_hosted as hosted
from .assistant_calendar import CalendarTools, calendar_scope
from .assistant_voice import Recorder
from .appearance import button, label
from .dialogs import PlannerDialog, field
from .storage import Store, StoreError


def note(text):
    widget = label(text, "caption")
    widget.setWordWrap(True)
    return widget


class AssistantSettings(PlannerDialog):
    activation_finished = Signal(bool, str)

    def __init__(self, parent):
        super().__init__(parent, "Assistant settings")
        self.body.addWidget(label("Assistant settings", "heading"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        contents = QWidget()
        layout = QVBoxLayout(contents)
        self.provider = QComboBox()
        self.provider.addItems(["Personal project key", "Hosted beta"])
        self.provider.setCurrentIndex(1 if hosted.provider() == "Hosted" else 0)
        layout.addLayout(field("Assistant provider", self.provider))
        self.personal = QWidget()
        form = QVBoxLayout(self.personal)
        layout.addWidget(self.personal)
        scroll.setWidget(contents)
        self.body.addWidget(scroll, 1)
        saved = config.options()
        self.mode = QComboBox()
        self.mode.addItems(config.MODES)
        self.mode.setCurrentText(saved["mode"])
        form.addLayout(field("Model routing", self.mode))
        form.addWidget(
            note(
                "Auto uses Routine for everyday requests and Planning for scheduling constraints. GPT-5.4 selects gpt-5.4 directly."
            )
        )
        self.routine = QLineEdit(saved["routine"])
        self.planning = QLineEdit(saved["planning"])
        form.addLayout(field("Routine model", self.routine))
        form.addLayout(field("Planning model", self.planning))
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Enter or replace a project key")
        form.addLayout(field("OpenAI project API key", self.key))
        self.key_status = note("")
        form.addWidget(self.key_status)
        self.remove = button("Remove saved key")
        self.remove.clicked.connect(self.remove_saved)
        form.addWidget(self.remove)
        form.addWidget(
            note("Use the project linked to your API credit. Windows protects the saved key for this user.")
        )
        form.addWidget(note(config.PRIVACY))
        form.addWidget(note(config.SHARING))
        links = QLabel(
            '<a href="https://platform.openai.com/settings/organization/data-controls/sharing">Project sharing</a>  ·  <a href="https://platform.openai.com/usage">Usage and costs</a>'
        )
        links.setOpenExternalLinks(True)
        form.addWidget(links)
        self.beta = QWidget()
        beta = QVBoxLayout(self.beta)
        layout.addWidget(self.beta)
        beta.addWidget(note(hosted.CONSENT))
        beta.addWidget(note("All your devices share one allowance. The owner’s shared budget can run out first."))
        self.consent = QCheckBox("I agree to use the hosted service")
        self.consent.setChecked(hosted.provider() == "Hosted")
        beta.addWidget(self.consent)
        self.invite = QLineEdit()
        self.invite.setMaxLength(64)
        self.invite.setEchoMode(QLineEdit.EchoMode.Password)
        beta.addLayout(field("Single-use invitation", self.invite))
        self.enroll = button("Activate this PC")
        self.enroll.clicked.connect(self.activate_device)
        beta.addWidget(self.enroll)
        self.device_status = note("")
        beta.addWidget(self.device_status)
        self.unlink = button("Forget hosted access on this PC")
        self.unlink.clicked.connect(self.forget_device)
        beta.addWidget(self.unlink)
        self.activation = None
        self.activation_finished.connect(self.activated)
        self.finished.connect(lambda _: self.activation.set() if self.activation else None)
        self.error = self.error_label()
        self.body.addWidget(self.error)
        self.save_button = button("Save settings", "primary")
        self.save_button.clicked.connect(self.save)
        self.body.addWidget(self.save_button)
        self.provider.currentIndexChanged.connect(self.refresh_provider)
        self.refresh_provider()
        self.key_state()
        self.resize(475, min(650, parent.height()))

    def refresh_provider(self):
        beta = self.provider.currentIndex() == 1
        self.personal.setVisible(not beta)
        self.beta.setVisible(beta)
        self.device_status.setText("This PC is activated." if hosted.active() else "Ask the owner for an invitation.")
        self.unlink.setVisible(hosted.active())

    def forget_device(self):
        try:
            hosted.forget()
            self.refresh_provider()
        except OSError:
            self.device_status.setText("Windows could not remove the local credential.")

    def activate_device(self):
        if self.activation:
            return
        token = self.activation = api.Cancellation()
        invitation, accepted = self.invite.text(), self.consent.isChecked()
        self.beta.setEnabled(False)
        self.provider.setEnabled(False)
        self.save_button.setEnabled(False)
        self.device_status.setText("Activating…")
        def work():
            try:
                hosted.activate(invitation, accepted, token)
                ok, hint = True, "Activated. Save settings to use the hosted beta."
            except (ValueError, OSError) as exc:
                ok, hint = False, str(exc) if isinstance(exc, ValueError) else "Activation could not finish. Retry the same invitation."
            if not token.is_set():
                try:
                    self.activation_finished.emit(ok, hint)
                except RuntimeError:
                    pass
        threading.Thread(target=work, daemon=True, name="Calendar assistant activation").start()

    def activated(self, ok, hint):
        self.activation = None
        self.beta.setEnabled(True)
        self.provider.setEnabled(True)
        self.save_button.setEnabled(True)
        if ok:
            self.invite.clear()
        self.refresh_provider()
        self.device_status.setText(hint)

    def key_state(self):
        saved = (config.directory() / "project.key.dpapi").is_file()
        self.remove.setEnabled(saved)
        self.key_status.setText(
            "Saved for this Windows user"
            if saved
            else "Using OPENAI_API_KEY from Windows"
            if config.project_key()
            else "No project key configured"
        )

    def remove_saved(self):
        try:
            config.remove_key()
            self.key.clear()
            self.key_state()
        except OSError:
            self.error.setText("Windows could not remove the saved key.")
            self.error.show()

    def save(self):
        try:
            if self.provider.currentIndex() == 1:
                hosted.select("Hosted", self.consent.isChecked())
                self.accept()
                return
            prefs = config.validate_options(
                {
                    "mode": self.mode.currentText(),
                    "routine": self.routine.text().strip(),
                    "planning": self.planning.text().strip(),
                }
            )
            if self.key.text():
                config.save_key(self.key.text().strip())
            config.save_options(prefs)
            hosted.select("Personal")
        except (ValueError, OSError) as exc:
            self.error.setText(
                str(exc) if isinstance(exc, ValueError) else "Windows could not save assistant settings."
            )
            self.error.show()
            return
        self.key.clear()
        self.accept()


class AssistantPanel(QFrame):
    finished = Signal(object, str, object)
    transcribing = Signal(object)

    def __init__(self, window):
        super().__init__(window.centralWidget())
        self.setObjectName("assistantDrawer")
        self.window = window
        self.history = []
        self.plan = None
        self.token = None
        self.stop = None
        self.scope = calendar_scope(window.store)
        self.pending_text = ""
        self.history_provider = None
        self.hosted_attempt = None
        self.finished.connect(self.receive)
        self.transcribing.connect(self.transcription_progress)
        box = QVBoxLayout(self)
        box.setContentsMargins(14, 12, 14, 12)
        head = QHBoxLayout()
        head.addWidget(label("Assistant", "heading"))
        head.addStretch()
        clear = button("Clear chat", "link")
        clear.clicked.connect(self.clear)
        head.addWidget(clear)
        done = button("Close", "link")
        done.clicked.connect(self.dismiss)
        head.addWidget(done)
        box.addLayout(head)
        self.chat = QPlainTextEdit()
        self.chat.document().setMaximumBlockCount(400)
        self.chat.setReadOnly(True)
        self.chat.setAccessibleName("Conversation")
        self.chat.setPlaceholderText("Ask about your week, find a free hour, or plan a change.")
        box.addWidget(self.chat, 2)
        self.review_label = label("Review changes", "subheading")
        box.addWidget(self.review_label)
        self.review = QPlainTextEdit()
        self.review.setReadOnly(True)
        self.review.setAccessibleName("Proposed calendar changes")
        box.addWidget(self.review, 2)
        row = QHBoxLayout()
        self.apply_button = button("Apply", "primary")
        self.apply_button.clicked.connect(self.apply)
        self.discard = button("Discard")
        self.discard.clicked.connect(self.discard_plan)
        row.addWidget(self.apply_button)
        row.addWidget(self.discard)
        box.addLayout(row)
        self.status = note(
            "Messages and relevant calendar details go to OpenAI. Stopping a recording sends the audio for transcription."
        )
        self.status.setAccessibleName("Assistant status")
        box.addWidget(self.status)
        self.composer = QLineEdit()
        self.composer.setMaxLength(2400)
        self.composer.setPlaceholderText("Ask about your calendar…")
        self.composer.setAccessibleName("Message")
        self.composer.returnPressed.connect(self.send)
        self.composer.textChanged.connect(self.update_hint)
        box.addWidget(self.composer)
        row = QHBoxLayout()
        self.mic_button = button("Talk")
        self.mic_button.setToolTip(
            "Hold to record and release to transcribe, or click to start and use Stop."
        )
        self.mic_button.pressed.connect(self.record)
        self.mic_button.released.connect(self.release_mic)
        self.stop_button = button("Stop")
        self.stop_button.clicked.connect(self.stop_record)
        self.cancel_button = button("Cancel")
        self.cancel_button.clicked.connect(self.cancel)
        self.send_button = button("Send", "primary")
        self.send_button.clicked.connect(self.send)
        for widget in (self.mic_button, self.stop_button, self.cancel_button):
            row.addWidget(widget)
        row.addStretch()
        row.addWidget(self.send_button)
        box.addLayout(row)
        settings = button("Assistant settings", "link")
        settings.clicked.connect(lambda: window._present(AssistantSettings(window)))
        box.addWidget(settings)
        self.controls()

    def update_hint(self, text):
        # A tokenizer is deliberately not required for this rough, draft-only count.
        approx = (len(text.encode("utf-8")) + 3) // 4
        self.send_button.setToolTip(
            f"Estimated draft tokens: ~{approx}. Excludes context, tools and output. "
            "API usage appears after a successful response."
        )

    def controls(self):
        busy = self.token is not None
        for w in (self.review_label, self.review, self.apply_button, self.discard):
            w.setVisible(self.plan is not None)
        self.send_button.setEnabled(not busy and not self.plan)
        self.composer.setReadOnly(busy)
        self.mic_button.setEnabled(self.stop is not None or (not busy and not self.plan))
        self.stop_button.setVisible(self.stop is not None)
        self.cancel_button.setVisible(busy)

    def position(self):
        area = self.window.centralWidget()
        top = self.window.assistant_button.geometry().bottom() + 9
        width = max(335, min(435, int(area.width() * 0.40)))
        self.setGeometry(area.width() - width - 12, top, width, max(250, area.height() - top - 12))
        self.raise_()

    def check_scope(self):
        current = calendar_scope(self.window.store)
        if self.scope != current:
            self.clear()
            self.scope = current
            self.status.setText("Calendar changed. Start a new conversation.")

    def append(self, who, text):
        self.chat.appendPlainText(f"{who}\n{text}\n")
        self.chat.moveCursor(QTextCursor.MoveOperation.End)

    def discard_plan(self):
        self.plan = None
        self.review.clear()
        self.controls()
        self.status.setText("Proposal discarded.")

    def clear(self):
        self.cancel()
        self.history.clear()
        self.hosted_attempt = None
        self.pending_text = ""
        self.discard_plan()
        self.chat.clear()
        self.composer.clear()
        self.status.setText("Chat cleared.")

    def dismiss(self):
        if self.token:
            self.cancel()
        self.hide()

    def cancel(self):
        if self.token:
            self.token.set()
            self.status.setText("Cancelled.")
        self.token = self.stop = None
        self.controls()

    def start(self, kind, fn):
        token = self.token = api.Cancellation()
        self.controls()

        def work():
            try:
                result = fn(token)
                token.check()
            except InterruptedError:
                return
            except (api.ApiError, StoreError, ValueError) as exc:
                result = str(exc)
                action = "error"
            except Exception:
                result = "Could not complete the request. Your draft is available to retry."
                action = "error"
            else:
                action = kind
            if not token.is_set():
                try:
                    self.finished.emit(token, action, result)
                except RuntimeError:
                    pass  # Window closed while the worker was returning.

        threading.Thread(target=work, daemon=True, name="Calendar assistant").start()

    def send(self):
        self.check_scope()
        text = self.composer.text().strip()
        if self.token or self.plan or not text:
            return
        scope = self.scope
        path = self.window.store.path
        today = self.window.clock()
        provider = hosted.provider()
        if provider != self.history_provider:
            self.history.clear()
            self.hosted_attempt = None
            self.history_provider = provider
        history = list(self.history)
        self.pending_text = text
        self.status.setText(f"{provider} · Checking your request…")
        if provider == "Hosted":
            if self.hosted_attempt is None or self.hosted_attempt[0] != text:
                self.hosted_attempt = (text, hosted.turn_id())
            turn = hosted.Turn(self.hosted_attempt[1], today)

        def ask(token):
            with closing(Store(path)) as store:
                tools = CalendarTools(store, today)
            if tools.scope != scope:
                raise ValueError("Calendar changed. Send the request again.")
            if provider == "Hosted":
                reply, plan, status = api.run(tools, hosted.credential(), config.DEFAULTS, history, text, token, turn.respond)
                return reply, plan, "Hosted · " + status
            if provider != "Personal":
                raise ValueError("Select an assistant provider in Settings.")
            return api.run(tools, config.project_key(), config.options(), history, text, token)

        self.start("response", ask)

    def record(self):
        self.check_scope()
        if self.token or self.plan:
            return
        provider = hosted.provider()
        try:
            if provider not in ("Personal", "Hosted"):
                raise ValueError("Select an assistant provider in Settings.")
            key = hosted.credential() if provider == "Hosted" else config.project_key()
            if not key:
                raise ValueError("Add your project API key in Assistant settings.")
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))
            return
        self.pressed_at = monotonic()
        stop = self.stop = threading.Event()
        self.status.setText("Recording · release after holding, or choose Stop. Maximum 45 seconds.")

        def listen(token):
            data = Recorder().capture(stop, token)
            token.check()
            self.transcribing.emit(token)
            if provider != hosted.provider():
                raise ValueError("Provider changed. Record again to use the selected provider.")
            if provider == "Hosted":
                return hosted.transcribe(hosted.credential(), data, token), "Hosted · gpt-transcribe"
            transcript = api.transcribe(key, data, token)
            with wave.open(io.BytesIO(data)) as wav:
                seconds = wav.getnframes() / wav.getframerate()
            return (
                transcript,
                f"gpt-transcribe · {seconds:.1f} s · Estimated list cost: US${seconds * 0.0045 / 60:.6f}",
            )

        self.start("voice", listen)

    def release_mic(self):
        if self.stop and monotonic() - self.pressed_at >= 0.4:
            self.stop_record()

    def stop_record(self):
        if self.stop:
            self.stop.set()
            self.transcription_progress(self.token)

    def transcription_progress(self, token):
        if token is self.token:
            self.stop = None
            self.status.setText("Transcribing…")
            self.controls()

    def receive(self, token, kind, result):
        self.check_scope()
        if token is not self.token:
            return
        self.token = self.stop = None
        if kind == "error":
            self.status.setText(result)
        elif kind == "voice":
            transcript, status = result
            draft = (self.composer.text().rstrip() + " " + transcript).strip()
            if len(draft) > self.composer.maxLength():
                self.append("Transcript", transcript)
                self.status.setText(
                    "Transcript is too long to append to this draft. Copy the part you want."
                )
            else:
                self.composer.setText(draft)
                self.status.setText("Review before sending. " + status)
        else:
            reply, self.plan, status = result
            self.hosted_attempt = None
            self.append("You", self.pending_text)
            self.append("Assistant", reply)
            self.history = (self.history + [("user", self.pending_text), ("assistant", reply)])[-6:]
            if self.plan:
                self.review.setPlainText(self.plan.review)
            self.composer.clear()
            self.status.setText(status)
        self.controls()
        self.composer.setFocus()

    def apply(self):
        if not self.plan:
            return
        try:
            changed = CalendarTools(self.window.store).apply(self.plan)
        except StoreError as exc:
            if "fresh proposal" in str(exc):
                self.plan = None
                if not self.composer.text():
                    self.composer.setText(self.pending_text)
            self.status.setText(str(exc))
        else:
            self.plan = None
            self.append("Calendar", "Changes saved." if changed else "These changes were already applied.")
            self.history.append(("assistant", "The reviewed changes were saved locally."))
            self.status.setText("Saved to this calendar.")
            self.window.reload()
        self.controls()
