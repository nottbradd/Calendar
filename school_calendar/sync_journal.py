"""Local causal history. All mutating functions run inside the caller's SQLite transaction."""
import json
import re
from datetime import date, time
from uuid import uuid4

from .model import Entry, NEUTRAL_COLOUR


def packed(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def create_schema(db):
    # executescript would implicitly commit the surrounding migration.
    for sql in (
        "ALTER TABLE subjects ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0 CHECK(hidden IN (0,1))",
        "CREATE TABLE sync_profile (slot INTEGER PRIMARY KEY CHECK(slot=1), device TEXT NOT NULL, phase TEXT NOT NULL, secret BLOB NOT NULL, cursor INTEGER NOT NULL DEFAULT 0, contact TEXT)",
        "CREATE TABLE sync_ops (op TEXT PRIMARY KEY, entity TEXT NOT NULL, parents TEXT NOT NULL, payload TEXT NOT NULL, pending INTEGER NOT NULL)",
        "CREATE INDEX sync_entities ON sync_ops(entity,op)",
        "CREATE INDEX sync_pending ON sync_ops(pending) WHERE pending>0",
        "CREATE TABLE sync_edges (entity TEXT NOT NULL, parent TEXT NOT NULL, child TEXT NOT NULL, PRIMARY KEY(entity,parent,child))",
    ):
        db.execute(sql)


def create_history_schema(db):
    # The anchor is a public commitment to the calendar ID and its original key.
    # No device credential or decryption key is needed to keep editing offline.
    for sql in (
        """CREATE TABLE sync_scopes (anchor TEXT PRIMARY KEY, selected INTEGER NOT NULL CHECK(selected IN (0,1)),
                                     verified INTEGER NOT NULL CHECK(verified IN (0,1)))""",
        "CREATE UNIQUE INDEX sync_selected ON sync_scopes(selected) WHERE selected=1",
        """CREATE TABLE sync_parked_ops (anchor TEXT NOT NULL REFERENCES sync_scopes(anchor),
                    op TEXT NOT NULL, entity TEXT NOT NULL, parents TEXT NOT NULL, payload TEXT NOT NULL,
                    pending INTEGER NOT NULL, PRIMARY KEY(anchor,op))""",
        # IDs can legitimately exist in two calendars. Keep a local revision floor so a
        # stale editor cannot save or delete a different calendar's newly projected row.
        """CREATE TRIGGER retire_entry_revision BEFORE DELETE ON entries
           WHEN EXISTS (SELECT 1 FROM sync_scopes) OR EXISTS (SELECT 1 FROM sync_profile)
           BEGIN INSERT INTO preferences(key,value) VALUES ('retired_entry_revision',OLD.revision)
                 ON CONFLICT(key) DO UPDATE SET value=OLD.revision WHERE CAST(value AS INTEGER)<OLD.revision; END""",
        """CREATE TRIGGER start_entry_revision AFTER INSERT ON entries
           WHEN NEW.revision <= (SELECT CAST(value AS INTEGER) FROM preferences WHERE key='retired_entry_revision')
           BEGIN UPDATE entries SET revision=(SELECT CAST(value AS INTEGER)+1 FROM preferences
                 WHERE key='retired_entry_revision') WHERE id=NEW.id; END""",
    ):
        db.execute(sql)


def selected_scope(db):
    return db.execute("SELECT anchor,verified FROM sync_scopes WHERE selected=1").fetchone()


def bind_scope(db, anchor, verified):
    db.execute("INSERT INTO sync_scopes(anchor,selected,verified) VALUES (?,1,?)", (anchor, verified))


def activate_scope(store, anchor):
    """Select the verified calendar's journal and projection, in the activation transaction."""
    db = store.db
    previous = selected_scope(db)
    if previous is None:  # A 1.3.0 profile: its existing journal already belongs to this identity.
        bind_scope(db, anchor, True)
        return
    if previous["anchor"] == anchor:
        if not previous["verified"]:
            db.execute("UPDATE sync_scopes SET verified=1 WHERE anchor=?", (anchor,))
        return

    # Only the selected calendar's operations may appear in the outbox. Preserve their
    # insertion order (parents before locally created children) and stable IDs on every switch.
    columns = "op,entity,parents,payload,pending"
    db.execute(f"INSERT INTO sync_parked_ops(anchor,{columns}) SELECT ?,{columns} FROM sync_ops ORDER BY rowid",
               (previous["anchor"],))
    db.execute("DELETE FROM sync_edges")
    db.execute("DELETE FROM sync_ops")
    db.execute("UPDATE sync_scopes SET selected=0 WHERE selected=1")
    returning = db.execute("UPDATE sync_scopes SET selected=1,verified=1 WHERE anchor=?", (anchor,)).rowcount
    if not returning:
        bind_scope(db, anchor, True)
        if not previous["verified"]:
            # An initial, unsuccessful enrollment never sent data. The original standalone
            # entries still get their first import; its provisional journal stays parked.
            seed(store)
            return
    else:
        db.execute(f"INSERT INTO sync_ops({columns}) SELECT {columns} FROM sync_parked_ops WHERE anchor=? ORDER BY rowid",
                   (anchor,))
        db.execute("""INSERT INTO sync_edges(entity,parent,child)
                      SELECT o.entity,p.value,o.op FROM sync_ops o, json_each(o.parents) p""")
        db.execute("DELETE FROM sync_parked_ops WHERE anchor=?", (anchor,))

    # Rebuild from this calendar's heads, including unsent deletions. A replayed operation
    # is a duplicate and will not project again. Device-local preferences are independent.
    db.execute("DELETE FROM entries")
    db.execute("DELETE FROM subjects")
    for entity, in db.execute("SELECT DISTINCT entity FROM sync_ops ORDER BY entity DESC"):
        project(db, entity)  # s: subjects before e: entries


def entry_value(entry):
    return dict(title=entry.title, day=entry.day.isoformat(), subject=entry.subject,
                kind=entry.kind, at=entry.at.isoformat(timespec="minutes") if entry.at else None,
                end_at=entry.end_at.isoformat(timespec="minutes") if entry.end_at else None,
                notes=entry.notes, custom_kind=entry.custom_kind, completed=entry.completed)


def decode_entry(identifier, value):
    data = dict(value)
    data["day"] = date.fromisoformat(data["day"])
    for field in ("at", "end_at"):
        data[field] = time.fromisoformat(data[field]) if data[field] is not None else None
    if type(data["completed"]) is not bool:
        raise ValueError("Invalid completion value.")
    return Entry(id=identifier, **data).cleaned()


HEAD = """NOT EXISTS (SELECT 1 FROM sync_edges e WHERE e.entity=o.entity AND e.parent=o.op)"""


def heads(db, entity):
    return db.execute("SELECT op,payload,parents,pending FROM sync_ops o WHERE entity=? AND " + HEAD +
                      " ORDER BY (payload='null') DESC, op", (entity,)).fetchall()


def conflicts(db):
    return [r[0] for r in db.execute("SELECT entity FROM sync_ops o WHERE " + HEAD +
                                    " GROUP BY entity HAVING count(DISTINCT payload)>1 ORDER BY entity")]


def insert(db, op, entity, parents, payload, pending):
    existing = db.execute("SELECT entity,parents,payload FROM sync_ops WHERE op=?", (op,)).fetchone()
    fields = (entity, packed(sorted(parents)), packed(payload))
    if existing:
        if tuple(existing) != fields:
            raise ValueError("An operation identifier was reused with different content.")
        return False
    db.execute("INSERT INTO sync_ops VALUES (?,?,?,?,?)", (op, *fields, pending))
    db.executemany("INSERT INTO sync_edges VALUES (?,?,?)", ((entity, p, op) for p in parents))
    return True


def local_version(db, entity, versions, value):
    parents = set()
    for row in versions:
        if row["pending"] == 2:
            # This oversized draft could never have left this PC. A subsequent edit replaces
            # it, based on the last versions that other devices could actually have seen.
            parents.update(json.loads(row["parents"]))
            db.execute("DELETE FROM sync_edges WHERE entity=? AND child=?", (entity, row["op"]))
            db.execute("DELETE FROM sync_ops WHERE op=?", (row["op"],))
        else:
            parents.add(row["op"])
    parents = sorted(parents)
    size = len(packed({"entity": entity, "parents": parents, "value": value}).encode())
    # XChaCha20-Poly1305 adds a 24-byte nonce and a 16-byte tag before unpadded Base64.
    pending = 1 if ((size + 40) * 8 + 5) // 6 <= 700000 else 2
    insert(db, uuid4().hex, entity, parents, value, pending)


def record(db, entity, payload):
    if not selected_scope(db) and not db.execute("SELECT 1 FROM sync_profile").fetchone():
        return
    versions = heads(db, entity)
    if len({r["payload"] for r in versions}) > 1:
        raise ValueError("Resolve the competing versions in Settings → Sync before editing this item.")
    if entity.startswith("e:") and versions and all(r["payload"] == "null" for r in versions) and payload is not None:
        raise ValueError("This entry was deleted. Create a new entry to restore a copy.")
    local_version(db, entity, versions, payload)


def seed_subject(db, row):
    entity = "s:" + row["key"]
    record(db, entity, {"name": row["name"], "colour": row["colour"]})
    if row["hidden"]:
        record(db, entity, None)


def seed(store):
    for subject in store.db.execute("SELECT key,name,colour,hidden FROM subjects ORDER BY key"):
        seed_subject(store.db, subject)
    for entry in store.stored_entries():
        record(store.db, "e:" + entry.id, entry_value(entry))


def repair_legacy_subject_links(store):
    """Repair only associations still provable from the 1.3.0 local database."""
    db = store.db
    for subject in db.execute("SELECT key,name,colour,hidden FROM subjects WHERE hidden=1"):
        if not heads(db, "s:" + subject["key"]):
            seed_subject(db, subject)
    for entry in store.stored_entries():
        if not entry.subject:
            continue
        entity, value = "e:" + entry.id, entry_value(entry)
        versions = heads(db, entity)
        # Do not overwrite a competing edit or guess a missing local foreign key.
        if versions and all(r["payload"] == packed({**value, "subject": ""}) for r in versions):
            local_version(db, entity, versions, value)


def validate(entity, value):
    if not isinstance(entity, str) or not entity[2:]:
        raise ValueError("Invalid object identifier.")
    if entity.startswith("e:"):
        if value is not None:
            decode_entry(entity[2:], value)
    elif entity.startswith("s:"):
        if value is None:
            return
        if (set(value) != {"name", "colour"} or value["name"].casefold() != entity[2:]
                or not re.fullmatch(r"#[a-fA-F0-9]{6}", value["colour"])):
            raise ValueError("Invalid subject version.")
    else:
        raise ValueError("Invalid calendar object.")


def project(db, entity):
    # An unresolved edit/delete conflict stays deleted until explicitly restored.
    version = heads(db, entity)[0]
    winner = json.loads(version["payload"])
    identifier = entity[2:]
    if entity.startswith("s:"):
        if winner is None:
            # A hidden seed is a known name/colour followed by a removal. Keep its
            # metadata even if the child arrived before its parent or we rebuilt the view.
            prior = db.execute("""SELECT p.payload FROM sync_edges e JOIN sync_ops p ON p.op=e.parent
                                  AND p.entity=e.entity WHERE e.entity=? AND e.child=? AND p.payload!='null'
                                  ORDER BY p.op LIMIT 1""", (entity, version["op"])).fetchone()
            if prior:
                info = json.loads(prior["payload"])
                db.execute("""INSERT INTO subjects(key,name,colour,hidden) VALUES (?,?,?,1)
                              ON CONFLICT(key) DO UPDATE SET name=excluded.name,colour=excluded.colour,hidden=1""",
                           (identifier, info["name"], info["colour"]))
            else:
                db.execute("""INSERT INTO subjects(key,name,colour,hidden) VALUES (?,?,?,1)
                              ON CONFLICT(key) DO UPDATE SET hidden=1""", (identifier, identifier, NEUTRAL_COLOUR))
        else:
            db.execute("""INSERT INTO subjects(key,name,colour) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET
                          name=excluded.name, colour=excluded.colour, hidden=0""", (identifier, winner["name"], winner["colour"]))
    elif winner is None:
        db.execute("DELETE FROM entries WHERE id=?", (identifier,))
    else:
        entry = decode_entry(identifier, winner)
        subject = entry.subject.casefold() or None
        if subject:
            # The subject's own operation can arrive after its first entry.
            db.execute("INSERT OR IGNORE INTO subjects(key,name,colour) VALUES (?,?,?)", (subject, entry.subject, NEUTRAL_COLOUR))
        db.execute("""INSERT INTO entries(id,title,day,at,end_at,subject_key,kind,custom_kind,notes,completed,revision)
                      VALUES (?,?,?,?,?,?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET
                      title=excluded.title,day=excluded.day,at=excluded.at,end_at=excluded.end_at,
                      subject_key=excluded.subject_key,kind=excluded.kind,custom_kind=excluded.custom_kind,
                      notes=excluded.notes,completed=excluded.completed,revision=entries.revision+1""",
                   (identifier, entry.title, winner["day"], winner["at"], winner["end_at"], subject,
                    entry.kind, entry.custom_kind, entry.notes, int(entry.completed)))


def receive(db, op, content):
    entity, parents, value = content["entity"], content["parents"], content["value"]
    if (not re.fullmatch("[0-9a-f]{32}", op) or not isinstance(parents, list) or op in parents or
            len(set(parents)) != len(parents) or any(not re.fullmatch("[0-9a-f]{32}", p) for p in parents)):
        raise ValueError("Invalid causal history.")
    validate(entity, value)
    if insert(db, op, entity, parents, value, False):
        project(db, entity)


def resolve(db, entity, expected, chosen):
    with db:
        db.execute("BEGIN IMMEDIATE")
        rows = heads(db, entity)
        versions = {r["op"]: r["payload"] for r in rows}
        if set(versions) != set(expected) or chosen not in versions:
            raise ValueError("The versions changed. Reopen this conflict to compare them again.")
        local_version(db, entity, rows, json.loads(versions[chosen]))
        project(db, entity)
