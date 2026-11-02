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
    if not db.execute("SELECT 1 FROM sync_profile").fetchone():
        return
    versions = heads(db, entity)
    if len({r["payload"] for r in versions}) > 1:
        raise ValueError("Resolve the competing versions in Settings → Sync before editing this item.")
    if entity.startswith("e:") and versions and all(r["payload"] == "null" for r in versions) and payload is not None:
        raise ValueError("This entry was deleted. Create a new entry to restore a copy.")
    local_version(db, entity, versions, payload)


def seed(store):
    snapshot = store._snapshot()
    for name, colour in snapshot.colours.items():
        record(store.db, "s:" + name.casefold(), {"name": name, "colour": colour})
    for entry in snapshot.entries:
        record(store.db, "e:" + entry.id, entry_value(entry))


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
    winner = json.loads(heads(db, entity)[0]["payload"])
    identifier = entity[2:]
    if entity.startswith("s:"):
        if winner is None:
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
