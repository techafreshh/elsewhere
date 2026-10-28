/** Tier-1 state-driven AG-UI streaming client for POST /agui.
 *
 * Reads the SSE body chunk-by-chunk with a fetch ReadableStream reader and
 * dispatches each `data:` JSON event as it arrives, so TOOL_CALL_START/END
 * rows and STATE_DELTA progressive reveals render while the run is still
 * in flight. Mirrors the `_collect_events` framing in
 * backend/tests/test_agui.py (split on lines starting with `data:` and
 * `JSON.parse` each), plus a text buffer for lines split across chunks.
 * Never throws on blank/malformed lines — those are skipped silently.
 */

export interface AguiEvent {
  type: string;
  [key: string]: unknown;
}

export interface AguiActivity {
  toolCallId: string;
  toolCallName: string;
  phase: "start" | "end";
  done: boolean;
}

/** Parse one SSE line into an AG-UI event. Null on any mismatch. Never throws. */
export function parseAguiFrame(line: string): AguiEvent | null {
  try {
    const trimmed = line.trim();
    if (!trimmed.startsWith("data:")) return null;
    const jsonText = trimmed.slice("data:".length).trim();
    if (!jsonText) return null;
    const event = JSON.parse(jsonText) as Record<string, unknown>;
    if (!event || typeof event.type !== "string") return null;
    return event as AguiEvent;
  } catch {
    return null;
  }
}

/** Stream an SSE response, calling `onEvent` per parsed frame in arrival order.
 *
 * Throws on transport/read failures and propagates `onEvent` throws (the
 * caller treats both as "abandon the stream, fall back") but never throws
 * for blank or malformed lines. Always releases the reader lock. */
export async function streamAgui(
  response: Response,
  onEvent: (event: AguiEvent) => void,
): Promise<void> {
  const body = response.body;
  if (!body) {
    // No ReadableStream (non-streaming context): still dispatch per frame
    // so the same progressive path owns the payload.
    const text = await response.text();
    for (const line of text.split("\n")) {
      const event = parseAguiFrame(line);
      if (event) onEvent(event);
    }
    return;
  }
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        const event = parseAguiFrame(line);
        if (event) onEvent(event);
      }
    }
    buffer += decoder.decode();
    for (const line of buffer.split("\n")) {
      const event = parseAguiFrame(line);
      if (event) onEvent(event);
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* lock already released by cancel — nothing to do */
    }
  }
}
