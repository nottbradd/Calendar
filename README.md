# Calender

A local Windows planner for SACs, exams, assignments, deadlines, and custom events. Calender opens to today, with the month beside upcoming assessments. It runs offline.

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

- Choose **Add entry** or click an empty part of a calendar date. Enter a title and date, choose a type, and optionally add a subject, time, and notes. A blank time means all day. The **Custom** type accepts a name of your choice.
- The filled date badge marks today. An outlined cell marks the selected day. Use the arrows to change months or **Today** to return to the current date.
- The **Upcoming** agenda includes unfinished past work first, then today and future dates. Within each day, all-day entries come first, followed by timed entries in clock order. Ties sort by title and then stable entry ID.
- Click an entry to read its details, edit it, or mark it complete. Use the **Completed** tab to find and reopen finished work. Deletion asks for confirmation and initially focuses **Keep entry**.
- Choose **+N more** on a busy date or **View day** below the calendar to see every entry. Subjects keep their colour across the grid, agenda, day list, and details.

| Keyboard action | Shortcut |
| --- | --- |
| Add an entry for the selected day | Ctrl+N |
| Return to today | Ctrl+T |
| Previous or next month | Alt+Left / Alt+Right |
| Move the selected date with the month grid focused | Arrow keys |
| Open the selected day / add on that date | Enter / Space |
| Move between controls | Tab / Shift+Tab |
| Save the editor | Ctrl+Enter or Ctrl+S |
| Dismiss a dialog | Escape |

## Local data

Calender opens the original database at **%LOCALAPPDATA%\SchoolCalendar\calendar.sqlite3**. Upgrading from the previous version requires no import or migration. The source and standalone versions share this location. Entries, completion states, and subject colours are saved together in SQLite transactions. Dates are stored as calendar dates and times as local clock times. Countdowns use the computer’s local date.

To back up or move a calendar, close every Calender window and copy the database file. To restore it, close the app, keep a copy of your current database, and put the backup at the path above. If the app cannot read a file, it reports the error and preserves the file. A failed save leaves the editor open with your values.

## Appearance

Frosted glass is the default. On Windows 11 build 22621 or later, Calender requests Desktop Acrylic for the standard window frame and the content behind the controls. Windows controls the blur and can substitute a solid material when system transparency is disabled. On systems without the backdrop API, Calender paints softly tinted panels over an opaque background. [Windows backdrop support](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwm_systembackdrop_type) · [Acrylic system behaviour](https://learn.microsoft.com/en-us/windows/apps/design/style/acrylic)

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
