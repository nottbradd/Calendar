from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time
from uuid import uuid4


KINDS = ("SAC", "Exam", "Assignment", "Deadline", "Custom")
SUBJECT_COLOURS = (
    "#376BB4", "#377D70", "#946044", "#8463A6",
    "#A75476", "#717337", "#3F8196", "#756391",
)
COLOUR_NAMES = ("Blue", "Teal", "Clay", "Violet", "Rose", "Olive", "Sea", "Plum")
NEUTRAL_COLOUR = "#65758B"
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class Entry:
    title: str
    day: date
    subject: str = ""
    kind: str = "SAC"
    at: time | None = None
    notes: str = ""
    custom_kind: str = ""
    completed: bool = False
    id: str = field(default_factory=lambda: uuid4().hex)
    revision: int = 0
    end_at: time | None = None

    @property
    def type_label(self) -> str:
        return self.custom_kind or self.kind

    @property
    def time_label(self) -> str:
        if self.at is None:
            return "All day"
        return f"{self.at:%H:%M}–{self.end_at:%H:%M}" if self.end_at else f"{self.at:%H:%M}"

    @property
    def time_description(self) -> str:
        if self.at is not None and self.end_at is None:
            return f"{self.time_label} · No end time"
        return self.time_label

    def cleaned(self) -> Entry:
        if not self.title.strip():
            raise ValueError("Give this entry a title.")
        if type(self.day) is not date:
            raise ValueError("Choose a valid calendar date.")
        if self.kind not in KINDS:
            raise ValueError("Choose an entry type.")
        validate_times(self.at, self.end_at)
        return replace(
            self, title=self.title.strip(), subject=" ".join(self.subject.split()),
            notes=self.notes.strip(), custom_kind=self.custom_kind.strip() if self.kind == "Custom" else "",
        )


def order_key(entry: Entry) -> tuple:
    # All-day items precede even a timed item at midnight.
    return (entry.day, entry.at is not None, entry.at or time.min, entry.title.casefold(), entry.id)


def countdown(entry: Entry, today: date) -> str:
    if entry.completed:
        return "Completed"
    days = (entry.day - today).days
    if days < 0:
        return "1 day overdue" if days == -1 else f"{-days} days overdue"
    if days == 0:
        return "Today"
    return "Tomorrow" if days == 1 else f"In {days} days"


def parse_day(text: str) -> date:
    text = text.strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError("Enter a valid date in DD/MM/YYYY format.")


def validate_times(start: time | None, end: time | None) -> None:
    for value in (start, end):
        if value is not None and (value.tzinfo is not None or value.second or value.microsecond):
            raise ValueError("Use a local time in hours and minutes.")
    if end is not None and start is None:
        raise ValueError("Enter a start time before adding an end time.")
    if end is not None and end <= start:
        raise ValueError("End time must be later than the start time on the same date.")


def parse_time(text: str) -> time | None:
    text = text.strip()
    if not text:
        return None
    if not re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", text):
        raise ValueError("Use a 24-hour time such as 14:30, or leave this field blank.")
    hours, minutes = text.split(":")
    return time(int(hours), int(minutes))


def full_day(day: date) -> str:
    return f"{day:%A}, {day.day} {day:%B} {day.year}"


def shift_month(day: date, offset: int) -> date:
    index = (day.year - 1) * 12 + day.month - 1 + offset
    index = max(0, min(9999 * 12 - 1, index))
    year, month = divmod(index, 12)
    year, month = year + 1, month + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def month_grid(day: date) -> list[list[date | None]]:
    first = day.replace(day=1)
    start = first.toordinal() - first.weekday()
    rows = (first.weekday() + calendar.monthrange(day.year, day.month)[1] + 6) // 7
    return [
        [
            date.fromordinal(n) if 1 <= n <= date.max.toordinal() else None
            for n in range(start + 7 * row, start + 7 * row + 7)
        ]
        for row in range(rows)
    ]
