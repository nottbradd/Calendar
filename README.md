# Calender

A local Windows planner for SACs, exams, assignments, deadlines, and custom events. Calender opens to today in your last-used Month or Week view. It runs offline.

![Calender on Windows, showing a month and upcoming assessments with sample entries](docs/calender.png)

## Run on Windows

Double-click **Calender.exe** to run the standalone app. The build places it in **dist**. You can copy it to another folder. Python, the font, and the icon are included.

To run from source, use 64-bit Python 3.14 on Windows and open PowerShell in the project folder.

~~~powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m school_calendar
~~~

The last command launches the app on subsequent runs.

## Plan your work

- Choose **Add entry** or click an empty date. A title and date are required. You can also add a subject, type, and notes. The **Custom** type accepts a name of your choice.
- Leave both times blank for an all-day entry. Set only **Start** for a single point in time, or set **Start** and **End** for a range. Use local 24-hour times. The end must be later on the same date.
- The filled badge in Month marks today. An outlined cell marks the selected day. Use the arrows to move one month or week, or **Today** to return to the current date. Switching views keeps the selected date.
- The **Upcoming** agenda includes unfinished past work first, then today and future dates. Within each day, all-day entries come first, followed by timed entries in clock order. Ties sort by title and then stable entry ID.
- Click an entry to read its details, edit it, or mark it complete. Use the **Completed** tab to find and reopen finished work. Deletion asks for confirmation and initially focuses **Keep entry**.
- Choose **+N more** on a busy date or **View day** below the calendar to see every entry. Subjects keep their colour across the grid, agenda, day list, and details.

### Week

The week starts on Monday. All-day entries stay above the schedule as you scroll. The timeline opens at 09:00, and you can scroll from midnight to the end of the day. Click a day heading or the all-day strip to add an untimed entry. Click a free time slot to start an entry at that quarter hour.

A coloured block spans the exact time range. A hollow circle marks a start with no end time. Overlapping entries sit side by side. Even a one-minute entry has a clickable title and time below its thin duration mark. On smaller windows **Show agenda** opens Upcoming and Completed alongside the week. Crowded days allow horizontal scrolling.

![Calender week with a 10:15–11:45 lab, overlapping workshops and a start-only deadline](docs/week.png)

### Subjects

Open **Settings**, enter a subject name, choose a colour, and select **Add subject**. Saved subjects appear immediately in the **Subject** dropdown when you create or edit an entry. You can also type a subject directly into an entry to give it the next palette colour automatically.

![Settings with saved subjects and an eight-colour palette](docs/settings.png)

| Keyboard action | Shortcut |
| --- | --- |
| Add an entry for the selected day | Ctrl+N |
| Return to today | Ctrl+T |
| Previous or next month or week | Alt+Left / Alt+Right |
| Open Settings | Ctrl+, |
| Move the selected date with the month grid focused | Arrow keys |
| Open the selected day / add on that date | Enter / Space |
| Move between controls | Tab / Shift+Tab |
| Save the editor | Ctrl+Enter or Ctrl+S |
| Dismiss a dialog | Escape |

## Local data

Calender opens the original database at **%LOCALAPPDATA%\SchoolCalendar\calendar.sqlite3**. Version 1.2 upgrades earlier calendars in a single SQLite transaction. Existing entries retain their IDs, notes, completion state, start times and subject colours. Their end times stay blank. The source and standalone versions share this location. Dates are stored as calendar dates and times as local clock times. Countdowns use the computer’s local date.

To back up or move a calendar, close every Calender window and copy the database file. To restore it, close the app, keep a copy of your current database, and put the backup at the path above. If the app cannot read a file, it reports the error and preserves the file. A failed save leaves the editor open with your values.

## Appearance

Frosted glass is the default for the calendar, agenda, Settings and entry dialogs. On Windows 11 build 22621 or later, Desktop Acrylic lets colours from other windows or the wallpaper show through the active window with system blur. The panels and fields tint this material while text stays sharp. Windows can use a solid material when transparency is off or Battery Saver is active. Systems without the backdrop API use a painted gradient and translucent internal layers. [Windows backdrop support](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwm_systembackdrop_type) · [Acrylic system behaviour](https://learn.microsoft.com/en-us/windows/apps/design/style/acrylic)

The interface uses the bundled upright variable font from the [Source Sans 3 release](https://github.com/adobe-fonts/source-sans/releases/tag/3.052R). The [font licence](school_calendar/assets/fonts/LICENSE.md) is included.

## Verify and build

The app uses Python, PySide6 Essentials (Qt Widgets), and Python’s built-in SQLite driver. With the virtual environment above, run the tests in a Windows desktop session. The interface tests briefly open real windows and use temporary calendars.

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
~~~

Build and launch the standalone Windows executable with these commands.

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe .\scripts\build_windows.py
& ".\dist\Calender.exe"
~~~

The repository contains source, artwork, dependency pins, and tests. Build outputs go to the ignored build and dist folders.
