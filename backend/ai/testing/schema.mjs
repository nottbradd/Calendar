import { readFile, readdir } from "node:fs/promises";

export async function migrate(db) {
  const directory = new URL("../migrations/", import.meta.url);
  for (const name of (await readdir(directory)).filter((n) => n.endsWith(".sql")).sort()) {
    const sql = await readFile(new URL(name, directory), "utf8");
    for (const stmt of sql.split(/^-- next\r?$/m)) await db.prepare(stmt.trim()).run();
  }
}
