import unittest
from datetime import date, time

from school_calendar.model import Entry, countdown, month_grid, order_key, parse_day, parse_time, shift_month


class CalendarDayTests(unittest.TestCase):
    def test_countdowns_across_midnight_dst_and_year_boundaries(self):
        # Sydney changes its clocks on 4 October and 5 April 2026.
        for today, due in ((date(2026, 10, 3), date(2026, 10, 4)),
                           (date(2026, 4, 4), date(2026, 4, 5)), (date(2026, 12, 31), date(2027, 1, 1))):
            self.assertEqual(countdown(Entry("Exam", due, at=time(0)), today), "Tomorrow")
        today = date(2026, 10, 5)
        for day, expected in ((3, "2 days overdue"), (4, "1 day overdue"), (5, "Today"), (10, "In 5 days")):
            self.assertEqual(countdown(Entry("Exam", today.replace(day=day)), today), expected)
        self.assertEqual(countdown(Entry("Exam", today.replace(day=1), completed=True), today), "Completed")

    def test_same_day_order_includes_midnight_and_stable_ties(self):
        day = date(2028, 2, 29)
        items = [
            Entry("Z", day, at=time(9)), Entry("A", day, at=time(0)),
            Entry("b", day, id="2"), Entry("A", day, id="3"), Entry("a", day, id="1"),
        ]
        self.assertEqual([e.id for e in sorted(items, key=order_key)[:3]], ["1", "3", "2"])
        self.assertEqual([e.at for e in sorted(items, key=order_key)[3:]], [time(0), time(9)])

    def test_month_boundaries_leap_day_and_monday_first(self):
        self.assertEqual(shift_month(date(2028, 1, 31), 1), date(2028, 2, 29))
        self.assertEqual(shift_month(date(2026, 12, 31), 1), date(2027, 1, 31))
        weeks = month_grid(date(2026, 11, 1))
        self.assertEqual((len(weeks), weeks[0][0], weeks[-1][-1]), (6, date(2026, 10, 26), date(2026, 12, 6)))
        self.assertEqual(month_grid(date.min)[0][0], date.min)
        self.assertIsNone(month_grid(date.max)[-1][-1])

    def test_form_dates_and_optional_clock_times(self):
        self.assertEqual(parse_day("29/02/2028"), parse_day("2028-02-29"))
        for value in ("", "31/02/2026", "11/21/2026"):
            with self.assertRaises(ValueError):
                parse_day(value)
        self.assertIsNone(parse_time(" "))
        self.assertEqual(parse_time("0:00"), time(0))
        self.assertEqual(parse_time("23:59"), time(23, 59))
        for value in ("24:00", "9:60", "14:30Z", "9"):
            with self.assertRaises(ValueError):
                parse_time(value)
