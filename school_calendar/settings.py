from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem

from .appearance import button, label, subject_icon
from .dialogs import PlannerDialog, field
from .model import COLOUR_NAMES, SUBJECT_COLOURS
from .storage import Store, StoreError


class SettingsDialog(PlannerDialog):
    subjects_changed = Signal()

    def __init__(self, parent, store: Store, colours):
        super().__init__(parent, "Settings")
        self.store = store
        heading = QHBoxLayout()
        heading.addWidget(label("Subjects", "heading"))
        heading.addStretch()
        self.count = label("", "count")
        heading.addWidget(self.count, 0, Qt.AlignmentFlag.AlignVCenter)
        self.body.addLayout(heading)
        self.subjects = QListWidget()
        self.subjects.setObjectName("subjects")
        self.subjects.setAccessibleName("Saved subjects")
        self.subjects.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.subjects.setIconSize(QSize(14, 14))
        self.subjects.setMinimumHeight(104)
        self.subjects.viewport().setAutoFillBackground(False)
        self.body.addWidget(self.subjects, 1)
        self.empty = label("Your subjects will appear here.", "muted")
        self.body.addWidget(self.empty)
        self.body.addSpacing(3)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. Biology")
        self.body.addLayout(field("New subject", self.name_input))
        self.swatch_group = QButtonGroup(self)
        self.swatches = []
        swatches = QHBoxLayout()
        swatches.setSpacing(3)
        swatches.addWidget(label("Colour", "field"))
        swatches.addSpacing(5)
        for index, (name, colour) in enumerate(zip(COLOUR_NAMES, SUBJECT_COLOURS)):
            option = button("", "swatch")
            option.setAccessibleName(name)
            option.setToolTip(name)
            option.setCheckable(True)
            option.setFixedSize(28, 28)
            option.setIconSize(QSize(22, 22))
            option.setIcon(subject_icon(colour, 22))
            self.swatch_group.addButton(option, index)
            self.swatches.append(option)
            swatches.addWidget(option)
        swatches.addStretch()
        self.body.addLayout(swatches)
        self.error = self.error_label()
        self.body.addWidget(self.error)
        actions = QHBoxLayout()
        self.feedback = label("", "muted")
        self.feedback.setWordWrap(True)
        self.feedback.setAccessibleName("Subject status")
        actions.addWidget(self.feedback, 1)
        self.done_button = button("Done")
        self.done_button.clicked.connect(self.accept)
        actions.addWidget(self.done_button)
        self.add_button = button("Add subject", "primary", "plus")
        self.add_button.clicked.connect(self.add_subject)
        self.name_input.returnPressed.connect(self.add_subject)
        actions.addWidget(self.add_button)
        self.body.addLayout(actions)
        self.refresh(colours)
        self.resize(406, 403)
        self.name_input.setFocus()

    def refresh(self, colours):
        self.subjects.clear()
        for name, colour in colours.items():
            item = QListWidgetItem(subject_icon(colour), name)
            shade = dict(zip(SUBJECT_COLOURS, COLOUR_NAMES)).get(colour, colour)
            item.setToolTip(f"{name} · {shade}")
            item.setData(Qt.ItemDataRole.AccessibleTextRole, item.toolTip())
            self.subjects.addItem(item)
        self.subjects.setVisible(bool(colours))
        self.empty.setVisible(not colours)
        self.count.setText(str(len(colours)))
        self.swatches[len(colours) % len(self.swatches)].setChecked(True)

    def add_subject(self):
        try:
            self.store.add_subject(self.name_input.text(), SUBJECT_COLOURS[self.swatch_group.checkedId()])
        except StoreError as exc:
            self.feedback.clear()
            self.error.setText(str(exc))
            self.error.show()
            self.name_input.setFocus()
            return
        self.error.hide()
        self.name_input.clear()
        self.subjects_changed.emit()
        try:
            self.refresh(self.store.read().colours)
        except StoreError as exc:
            self.error.setText(str(exc))
            self.error.show()
            return
        self.feedback.setText("Subject added.")
        self.name_input.setFocus()
