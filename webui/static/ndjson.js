// @ts-check
/**
 * Newline-delimited JSON from a streamed response: chunks can split a line
 * (or a multi-byte character) anywhere, so text is buffered until "\n".
 */

/**
 * @param {(event: any) => void} onEvent called with each parsed object
 * @param {number} [maxLine] longest accepted line, in characters
 */
export function createLineSplitter(onEvent, maxLine = 4 * 1024 * 1024) {
  let buffer = "";
  const emit = (/** @type {string} */ line) => {
    if (!line.trim()) return;
    const value = JSON.parse(line);
    if (value === null || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("The server sent an invalid event.");
    }
    onEvent(value);
  };
  return {
    /** @param {string} text */
    push(text) {
      buffer += text;
      let index;
      while ((index = buffer.indexOf("\n")) !== -1) {
        const line = buffer.slice(0, index);
        buffer = buffer.slice(index + 1);
        emit(line);
      }
      if (buffer.length > maxLine) throw new Error("The server sent an oversized event.");
    },
    end() {
      const rest = buffer;
      buffer = "";
      emit(rest);
    },
  };
}
