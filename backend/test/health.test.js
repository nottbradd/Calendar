import assert from "node:assert/strict";
import test from "node:test";
import worker from "../src/index.js";

const health = new Request("https://worker.test/health");

function database(first) {
  return {
    prepare(sql) {
      assert.equal(sql, "SELECT 1 AS ready");
      return { first };
    },
  };
}

test("health succeeds only after the bound database returns the probe value", async () => {
  const DB = database(async (column) => {
    assert.equal(column, "ready");
    return 1;
  });
  const response = await worker.fetch(health, { DB });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.deepEqual(await response.json(), { status: "ok" });
});

test("a missing binding, failed query or unexpected value yields a generic 503", async () => {
  const failed = database(async () => { throw new Error("private D1 error"); });
  const empty = database(async () => null);
  for (const env of [{}, { DB: failed }, { DB: empty }]) {
    const response = await worker.fetch(health, env);
    assert.equal(response.status, 503);
    assert.equal(response.headers.get("Cache-Control"), "no-store");
    assert.deepEqual(await response.json(), { status: "unavailable" });
  }
});

test("unknown routes and write requests are rejected before accessing D1", async () => {
  const env = {};
  const missing = await worker.fetch(new Request("https://worker.test/sync"), env);
  assert.equal(missing.status, 404);
  const write = await worker.fetch(new Request(health.url, { method: "POST" }), env);
  assert.equal(write.status, 405);
  assert.equal(write.headers.get("Allow"), "GET");
});
