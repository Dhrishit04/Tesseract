// Chat, model and capability requests to the Python sidecar.

import { SIDECAR_URL, SidecarError, sidecarJson } from "../lib/sidecar";

export interface HealthStatus {
  status: string;
  version: string;
}

export interface ModelInfo {
  id: string;
  name?: string;
}

export interface SkillInfo {
  name: string;
  description: string;
}

export type ToolStatus = "running" | "done" | "error";

export interface ToolEvent {
  name: string;
  status: ToolStatus;
  error?: string | null;
  preview?: string;
}

/** One event from /chat/stream. */
export type ChatEvent =
  | { kind: "content"; text: string }
  | { kind: "tool"; tool: ToolEvent }
  | { kind: "error"; message: string; code?: string };

export async function checkSidecarHealth(): Promise<HealthStatus> {
  return sidecarJson<HealthStatus>("/health");
}

export async function listModels(): Promise<ModelInfo[]> {
  const data = await sidecarJson<{ models?: ModelInfo[] }>("/models");
  return data.models ?? [];
}

export async function listSkills(): Promise<SkillInfo[]> {
  const data = await sidecarJson<{ skills?: SkillInfo[] }>("/skills");
  return data.skills ?? [];
}

function parseEvent(payload: string): ChatEvent | null {
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(payload);
  } catch {
    return null;
  }
  if (parsed.error) {
    return { kind: "error", message: String(parsed.message ?? "Unknown error"), code: parsed.code as string | undefined };
  }
  if (typeof parsed.content === "string") return { kind: "content", text: parsed.content };
  if (parsed.tool) return { kind: "tool", tool: parsed.tool as ToolEvent };
  return null;
}

/** Dispatch complete SSE lines. Returns true once the [DONE] marker arrives. */
function dispatchLines(lines: string[], onEvent: (event: ChatEvent) => void): boolean {
  for (const line of lines) {
    if (!line.startsWith("data: ")) continue;
    const payload = line.slice(6).trim();
    if (payload === "[DONE]") return true;
    const event = parseEvent(payload);
    if (event) onEvent(event);
  }
  return false;
}

/** Read server-sent events until [DONE] or the end of the stream. */
async function readEvents(reader: ReadableStreamDefaultReader<Uint8Array>, onEvent: (event: ChatEvent) => void) {
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    if (dispatchLines(lines, onEvent)) return;
  }
}

/** Stream a chat turn, calling onEvent for each server-sent event until [DONE]. */
export async function streamChat(
  request: { message: string; model: string; chat_id: string },
  onEvent: (event: ChatEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${SIDECAR_URL}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal,
    });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new SidecarError("The assistant service is not running.");
  }
  if (!response.ok || !response.body) {
    throw new SidecarError(`The assistant service returned ${response.status}.`, response.status);
  }

  await readEvents(response.body.getReader(), onEvent);
}
