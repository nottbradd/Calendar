# School Calendar

A local Windows planner for SACs, exams, assignments, deadlines, and custom events. The month calendar sits beside an agenda of unfinished work. The app runs offline and opens to today.

## Run on Windows

If you have the standalone build, double-click **School Calendar.exe**. The development build places it in **dist**. You can copy the executable to another folder and run it there. Python is included in the executable.

To run from source, use 64-bit Python 3.14 on Windows and open PowerShell in the project folder.

~~~powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m school_calendar
~~~

The last command launches the app on subsequent runs.

## Plan your work

- Choose **Add entry** or click an empty part of a calendar date. Enter a title and date, choose a type, and optionally add a subject, time, and notes. A blank time means all day. The **Custom** type accepts a name of your choice.
- The blue date badge marks today. An outlined cell marks the selected day. Use the arrows to change months or **Today** to return to the current date.
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

Both the source and standalone versions use **%LOCALAPPDATA%\SchoolCalendar\calendar.sqlite3**. Entries, completion states, and subject colours are saved together in SQLite transactions. Dates are stored as calendar dates and times as local clock times. Countdowns use the computer’s local date.

To back up or move a calendar, close every School Calendar window and copy the database file. To restore it, close the app, keep a copy of your current database, and put the backup at the path above. If the app cannot read a file, it reports the error and preserves the file. A failed save leaves the editor open with your values.

## Verify and build

The app uses Python, PySide6 Essentials (Qt Widgets), and Python’s built-in SQLite driver. With the virtual environment above, run the tests in a Windows desktop session. The interface tests briefly open real windows and use temporary calendars.

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
~~~

Build and launch the standalone Windows executable with these commands.

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe .\scripts\build_windows.py
& ".\dist\School Calendar.exe"
~~~

The repository contains source, artwork, dependency pins, and tests. Build outputs go to the ignored build and dist folders.
