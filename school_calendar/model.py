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

    @property
    def type_label(self) -> str:
        return self.custom_kind or self.kind

    @property
    def time_label(self) -> str:
        return self.at.strftime("%H:%M") if self.at is not None else "All day"

    def cleaned(self) -> Entry:
        if not self.title.strip():
            raise ValueError("Give this entry a title.")
        if type(self.day) is not date:
            raise ValueError("Choose a valid calendar date.")
        if self.kind not in KINDS:
            raise ValueError("Choose an entry type.")
        if self.at is not None and (self.at.tzinfo is not None or self.at.second or self.at.microsecond):
            raise ValueError("Use a local time in hours and minutes.")
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


def parse_time(text: str) -> time | None:
    text = text.strip()
    if not text:
        return None
    if not re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", text):
        raise ValueError("Use a 24-hour time such as 14:30, or leave it blank for all day.")
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
