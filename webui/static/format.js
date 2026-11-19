// @ts-check
/** Display formatting shared by the views; pure functions, tested in Node. */

/** @param {number | null | undefined} bytes */
export function formatBytes(bytes) {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes < 0) return "unknown";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value < 10 && unit ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

/** 6200 -> 6.2k, 16384 -> 16k, 950 -> 950. @param {number} count */
export function shortTokens(count) {
  if (count < 1000) return String(count);
  const value = count / 1000;
  return value < 10 && Math.round(value * 10) / 10 !== Math.trunc(value) ? `${value.toFixed(1)}k` : `${Math.round(value)}k`;
}

/**
 * "just now", "5 min ago", "3 h ago", "yesterday", or a date.
 * @param {string} iso
 * @param {number} [now]
 */
export function relativeTime(iso, now = Date.now()) {
  const time = Date.parse(iso);
  if (Number.isNaN(time)) return "";
  const seconds = Math.max(0, (now - time) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  if (seconds < 172800) return "yesterday";
  return new Date(time).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/**
 * Tokens per second and timings from Ollama's metadata (nanoseconds).
 * @param {Record<string, number> | null | undefined} metrics
 */
export function metricsRows(metrics) {
  if (!metrics) return [];
  /** @type {[string, string][]} */
  const rows = [];
  if (metrics.eval_count && metrics.eval_duration > 0) {
    rows.push(["Speed", `${(metrics.eval_count / (metrics.eval_duration / 1e9)).toFixed(1)} tokens/s`]);
  }
  if (typeof metrics.total_duration === "number") rows.push(["Total time", `${(metrics.total_duration / 1e9).toFixed(2)} s`]);
  if (typeof metrics.load_duration === "number") rows.push(["Model load", `${(metrics.load_duration / 1e9).toFixed(2)} s`]);
  if (typeof metrics.prompt_eval_count === "number") rows.push(["Prompt tokens", String(metrics.prompt_eval_count)]);
  if (typeof metrics.eval_count === "number") rows.push(["Answer tokens", String(metrics.eval_count)]);
  return rows;
}

/**
 * Nest flat project paths into folders for the file tree.
 * @param {{path: string, size_bytes: number}[]} files
 */
export function fileTree(files) {
  /** @typedef {{name: string, path: string, folders: Map<string, any>, files: {name: string, path: string, size_bytes: number}[]}} Folder */
  /** @type {Folder} */
  const root = { name: "", path: "", folders: new Map(), files: [] };
  for (const file of files) {
    const parts = file.path.split("/");
    let folder = root;
    parts.slice(0, -1).forEach((part, index) => {
      if (!folder.folders.has(part)) {
        folder.folders.set(part, { name: part, path: parts.slice(0, index + 1).join("/"), folders: new Map(), files: [] });
      }
      folder = folder.folders.get(part);
    });
    folder.files.push({ name: parts[parts.length - 1], path: file.path, size_bytes: file.size_bytes });
  }
  return root;
}

/**
 * Split a unified diff into typed lines for colouring.
 * @param {string} diff
 */
export function diffLines(diff) {
  return String(diff).split("\n").map((line) => ({
    text: line,
    kind: line.startsWith("+++") || line.startsWith("---") ? "file"
      : line.startsWith("@@") ? "hunk" : line.startsWith("+") ? "add" : line.startsWith("-") ? "remove" : "context",
  }));
}
