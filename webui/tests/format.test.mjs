import { test } from "node:test";
import assert from "node:assert/strict";
import { diffLines, fileTree, formatBytes, metricsRows, relativeTime, shortTokens } from "../static/format.js";
import { createLineSplitter } from "../static/ndjson.js";

test("bytes and tokens read naturally", () => {
  assert.equal(formatBytes(0), "0 B");
  assert.equal(formatBytes(1536), "1.5 KB");
  assert.equal(formatBytes(8 * 1024 ** 3), "8.0 GB");
  assert.equal(formatBytes(null), "unknown");
  assert.equal(shortTokens(950), "950");
  assert.equal(shortTokens(6200), "6.2k");
  assert.equal(shortTokens(16384), "16k");
});

test("relative times", () => {
  const now = Date.parse("2026-11-19T12:00:00Z");
  assert.equal(relativeTime("2026-11-19T11:59:30Z", now), "just now");
  assert.equal(relativeTime("2026-11-19T11:30:00Z", now), "30 min ago");
  assert.equal(relativeTime("2026-11-19T09:00:00Z", now), "3 h ago");
  assert.equal(relativeTime("2026-11-18T09:00:00Z", now), "yesterday");
  assert.equal(relativeTime("not a date", now), "");
});

test("metrics become readable rows", () => {
  const rows = metricsRows({ eval_count: 50, eval_duration: 2e9, total_duration: 3e9, prompt_eval_count: 12 });
  assert.deepEqual(rows[0], ["Speed", "25.0 tokens/s"]);
  assert.ok(rows.some(([label]) => label === "Prompt tokens"));
  assert.deepEqual(metricsRows(null), []);
});

test("flat paths nest into folders", () => {
  const tree = fileTree([{ path: "a.py", size_bytes: 1 }, { path: "src/b.py", size_bytes: 2 }, { path: "src/lib/c.py", size_bytes: 3 }]);
  assert.deepEqual(tree.files.map((f) => f.name), ["a.py"]);
  const src = tree.folders.get("src");
  assert.equal(src.path, "src");
  assert.equal(src.folders.get("lib").files[0].path, "src/lib/c.py");
});

test("diff lines are typed", () => {
  assert.deepEqual(diffLines("--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n same").map((l) => l.kind),
    ["file", "file", "hunk", "remove", "add", "context"]);
});

test("NDJSON events survive any chunk boundary", () => {
  const events = [];
  const splitter = createLineSplitter((event) => events.push(event));
  const wire = '{"type":"start"}\n{"type":"chunk","text":"hé"}\n\n{"type":"done"}';
  for (const piece of wire.match(/[\s\S]{1,3}/g)) splitter.push(piece);
  splitter.end();
  assert.deepEqual(events.map((e) => e.type), ["start", "chunk", "done"]);
  assert.equal(events[1].text, "hé");
});

test("NDJSON rejects non-objects and oversized lines", () => {
  assert.throws(() => createLineSplitter(() => {}).push("[1]\n"), /invalid event/);
  assert.throws(() => createLineSplitter(() => {}, 10).push("x".repeat(11)), /oversized/);
});
