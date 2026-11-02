// Calendar content is opaque. Only membership, key envelopes and feed positions are visible here.
const idPattern = /^[0-9a-f]{32}$/;
const keyPattern = /^[A-Za-z0-9_-]{43}$/;
const digestPattern = /^[0-9a-f]{64}$/;
const b64 = /^[A-Za-z0-9_-]+$/;
const MAX_MEMBERS = 12;
const MAX_BODY = 1024 * 1024;
const PAGE = 24;
const publicActions = new Set(["create", "claim", "recover"]);
const actions = new Set([...publicActions, "state", "invite", "approve", "revoke", "pull", "push"]);

class Failure extends Error {
  constructor(status, reason) { super(reason); this.status = status; }
}
function need(condition, status = 400, reason = "invalid_request") {
  if (!condition) throw new Failure(status, reason);
}
function reply(data, status = 200) {
  const headers = { "Cache-Control": "no-store" };
  if (status === 405) headers.Allow = "POST";
  if (status === 429) headers["Retry-After"] = "60";
  return Response.json(data, { status, headers });
}
function key(s) { return typeof s === "string" && keyPattern.test(s); }
function box(s, max = 32768) {
  return typeof s === "string" && s.length >= 56 && s.length <= max && b64.test(s);
}
async function digest(value) {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(bytes), n => n.toString(16).padStart(2, "0")).join("");
}
function same(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let different = 0;
  for (let i = 0; i < a.length; i++) different |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return different === 0;
}
async function input(request) {
  need(request.headers.get("Content-Type")?.split(";")[0] === "application/json", 415);
  need(Number(request.headers.get("Content-Length") || 0) <= MAX_BODY, 413);
  const reader = request.body?.getReader();
  need(reader);
  const chunks = [];
  let total = 0;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_BODY) { await reader.cancel(); throw new Failure(413, "too_large"); }
    chunks.push(value);
  }
  let parsed;
  try { parsed = JSON.parse(await new Blob(chunks).text()); } catch { throw new Failure(400, "invalid_json"); }
  need(parsed && typeof parsed === "object" && !Array.isArray(parsed));
  return parsed;
}
async function limit(db, request, calendar, action) {
  const minute = Math.floor(Date.now() / 60000);
  const network = request.headers.get("CF-Connecting-IP") || "local";
  const slot = await digest(minute + ":" + action + ":" + network);
  const budget = db.prepare("INSERT INTO sync_attempts VALUES (?,?,1) ON CONFLICT(window,slot) DO UPDATE SET hits=hits+1 RETURNING hits");
  const counts = await db.batch([
    db.prepare("DELETE FROM sync_attempts WHERE window < ?").bind(minute - 1),
    budget.bind(minute, slot), budget.bind(minute, action + ":" + calendar),
  ]);
  need(counts[1].results[0].hits <= (action === "create" ? 8 : 24) &&
       counts[2].results[0].hits <= 24, 429, "slow_down");
}
function state(directory, self, generation) {
  const pending = directory.invite?.until > Date.now() ? directory.invite.pending : null;
  return {
    epoch: directory.epoch, generation, box: self.box, recoveryPub: directory.recoveryPub, recoveryTag: directory.recoveryTag,
    members: directory.members.map(({ id, pub, tag }) => ({ id, pub, tag })),
    pending: pending ? { id: pending.id, pub: pending.pub } : null,
  };
}
async function changeDirectory(db, calendar, generation, directory) {
  const result = await db.prepare("UPDATE sync_calendars SET directory=?,generation=generation+1 WHERE id=? AND generation=?")
    .bind(JSON.stringify(directory), calendar, generation).run();
  need(result.meta.changes === 1, 409, "retry");
}
async function handle(request, db, calendar, action) {
  need(actions.has(action), 404, "not_found");
  need(request.method === "POST", 405, "method_not_allowed");
  if (publicActions.has(action)) await limit(db, request, calendar, action);
  const auth = /^Bearer ([a-f0-9]{32})\.([A-Za-z0-9_-]{43})$/.exec(request.headers.get("Authorization") || "");
  need(auth, 403, "denied");
  const [, device, secret] = auth;
  const verifier = await digest(secret);
  const body = await input(request);
  const row = await db.prepare("SELECT generation,directory FROM sync_calendars WHERE id=?").bind(calendar).first();
  if (!row && action === "create") {
    need(key(body.pub) && box(body.box) && key(body.recoveryPub) && box(body.recoveryBox) &&
         digestPattern.test(body.recoveryHash) && digestPattern.test(body.tag) && digestPattern.test(body.recoveryTag));
    const member = { id: device, pub: body.pub, verifier, box: body.box, tag: body.tag };
    const directory = { epoch: 1, members: [member], recoveryPub: body.recoveryPub,
      recoveryBox: body.recoveryBox, recoveryHash: body.recoveryHash, recoveryTag: body.recoveryTag, invite: null };
    const created = await db.prepare("INSERT OR IGNORE INTO sync_calendars(id,directory) VALUES (?,?)")
      .bind(calendar, JSON.stringify(directory)).run();
    need(created.meta.changes === 1, 409, "retry");
    return state(directory, member, 1);
  }
  need(row, 403, "denied");
  const dir = JSON.parse(row.directory);
  const gen = row.generation;
  const self = dir.members.find(m => m.id === device && same(m.verifier, verifier));
  if (self && ["state", "create", "claim", "recover"].includes(action)) return state(dir, self, gen);
  const pending = dir.invite?.pending;
  if (action === "claim") {
    need(key(body.ticket) && key(body.pub));
    need(dir.invite?.until > Date.now() && same(dir.invite.hash, await digest(body.ticket)), 403, "invalid_invite");
    need(!dir.members.some(m => m.id === device), 403, "denied");
    if (pending) {
      need(pending.id === device && pending.pub === body.pub && same(pending.verifier, verifier), 403, "invalid_invite");
    } else {
      dir.invite.pending = { id: device, pub: body.pub, verifier };
      await changeDirectory(db, calendar, gen, dir);
    }
    return { waiting: true };
  }
  if (action === "recover") {
    need(key(body.proof) && same(dir.recoveryHash, await digest(body.proof)), 403, "denied");
    if (!body.box) return { box: dir.recoveryBox, generation: gen, epoch: dir.epoch, recoveryPub: dir.recoveryPub };
    need(body.generation === gen, 409, "retry");
    need(key(body.pub) && box(body.box) && digestPattern.test(body.tag) && !dir.members.some(m => m.id === device));
    const member = { id: device, pub: body.pub, verifier, box: body.box, tag: body.tag };
    if (body.reset === true) {
      need(box(body.recoveryBox) && digestPattern.test(body.recoveryTag));
      dir.members = [];
      dir.invite = null;
      dir.epoch++;
      dir.recoveryBox = body.recoveryBox;
      dir.recoveryTag = body.recoveryTag;
    } else {
      need(dir.members.length <= MAX_MEMBERS, 409, "device_limit");
    }
    dir.members.push(member);
    await changeDirectory(db, calendar, gen, dir);
    return state(dir, member, gen + 1);
  }
  need(self, 403, "denied");
  if (action === "pull") {
    need(Number.isSafeInteger(body.after) && body.after >= 0);
    // Recheck the authenticated directory generation inside the data query.
    const result = await db.batch([
      db.prepare("SELECT generation FROM sync_calendars WHERE id=?").bind(calendar),
      db.prepare([
        "WITH candidates AS (SELECT seq,op,epoch,box FROM sync_changes WHERE calendar=? AND seq>?",
        "AND EXISTS (SELECT 1 FROM sync_calendars WHERE id=? AND generation=?) ORDER BY seq LIMIT ?)",
        "SELECT seq,op,epoch,CASE WHEN sum(length(CAST(box AS BLOB))) OVER (ORDER BY seq) <= 960000",
        "THEN box END AS box FROM candidates ORDER BY seq",
      ].join(" "))
        .bind(calendar, body.after, calendar, gen, PAGE + 1),
    ]);
    need(result[0].results[0]?.generation === gen, 409, "retry");
    const rows = result[1].results;
    const changes = rows.slice(0, PAGE).filter(item => item.box !== null);
    return { epoch: dir.epoch, generation: gen, changes, more: rows.length > changes.length };
  }
  if (action === "push") {
    need(body.epoch === dir.epoch, 409, "refresh_keys");
    need(Array.isArray(body.changes) && body.changes.length >= 1 && body.changes.length <= PAGE);
    for (const item of body.changes) need(item && idPattern.test(item.op) && box(item.box, 700000));
    const rows = body.changes.map(item => ({ op: item.op, box: item.box }));
    const result = await db.batch([
      db.prepare("SELECT generation FROM sync_calendars WHERE id=?").bind(calendar),
      db.prepare("INSERT INTO sync_changes(calendar,op,epoch,box) SELECT ?,json_extract(value,'$.op'),?,json_extract(value,'$.box') FROM json_each(?) WHERE EXISTS (SELECT 1 FROM sync_calendars WHERE id=? AND generation=?) ON CONFLICT(calendar,op) DO NOTHING")
        .bind(calendar, dir.epoch, JSON.stringify(rows), calendar, gen),
    ]);
    need(result[0].results[0]?.generation === gen, 409, "retry");
    return { accepted: rows.map(r => r.op) };
  }
  need(body.generation === gen, 409, "retry");
  if (action === "invite") {
    need(digestPattern.test(body.hash) && Number.isSafeInteger(body.seconds) && body.seconds >= 1 && body.seconds <= 600);
    need(dir.members.length < MAX_MEMBERS, 409, "device_limit");
    dir.invite = { hash: body.hash, until: Date.now() + body.seconds * 1000, pending: null };
  } else if (action === "approve") {
    need(dir.invite?.until > Date.now() && pending && pending.id === body.device && pending.pub === body.pub, 409, "invalid_invite");
    need(box(body.box) && digestPattern.test(body.tag) && dir.members.length < MAX_MEMBERS && !dir.members.some(m => m.id === pending.id));
    dir.members.push({ ...pending, box: body.box, tag: body.tag });
    dir.invite = null;
  } else if (action === "revoke") {
    need(body.device !== device && dir.members.some(m => m.id === body.device));
    const survivors = dir.members.filter(m => m.id !== body.device);
    need(body.boxes && body.tags && box(body.recoveryBox) && digestPattern.test(body.recoveryTag) &&
         survivors.every(m => box(body.boxes[m.id]) && digestPattern.test(body.tags[m.id])));
    for (const member of survivors) {
      member.box = body.boxes[member.id];
      member.tag = body.tags[member.id];
    }
    dir.members = survivors;
    dir.epoch++;
    dir.recoveryBox = body.recoveryBox;
    dir.recoveryTag = body.recoveryTag;
    dir.invite = null;
  } else {
    throw new Failure(403, "denied");
  }
  await changeDirectory(db, calendar, gen, dir);
  return state(dir, self, gen + 1);
}
export async function syncRequest(request, db, calendar, action) {
  try { return reply(await handle(request, db, calendar, action)); }
  catch (error) {
    if (error instanceof Failure) return reply({ error: error.message }, error.status);
    return reply({ error: "unavailable" }, 503);
  }
}
