CREATE TABLE sync_calendars (
  id TEXT PRIMARY KEY,
  generation INTEGER NOT NULL DEFAULT 1,
  directory TEXT NOT NULL
);
CREATE TABLE sync_changes (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  calendar TEXT NOT NULL REFERENCES sync_calendars(id) ON DELETE CASCADE,
  op TEXT NOT NULL,
  epoch INTEGER NOT NULL,
  box TEXT NOT NULL,
  UNIQUE(calendar, op)
);
CREATE INDEX sync_changes_feed ON sync_changes(calendar, seq);
CREATE TABLE sync_attempts (
  window INTEGER NOT NULL,
  slot TEXT NOT NULL,
  hits INTEGER NOT NULL,
  PRIMARY KEY(window, slot)
);
