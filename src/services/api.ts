// Chat, model and capability requests to the Python sidecar.

import { SIDECAR_URL, SidecarError, postJson, sidecarJson } from "../lib/sidecar";

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

export type ToolStatus = "running" | "done" | "error" | "declined";

export interface ToolEvent {
  name: string;
  status: ToolStatus;
  error?: string | null;
  preview?: string;
}

/** A change Tesseract wants to make, waiting for the owner's answer. */
export interface ApprovalRequest {
  id: string;
  tool: string;
  title: string;
  summary: string;
  details: Record<string, unknown>;
  irreversible: boolean;
}

export type ApprovalDecision = "allow" | "always" | "deny";

export interface ApprovalResolution {
  id: string;
  decision: ApprovalDecision | "other";
  via: "button" | "reply" | "timeout";
  /** Present only when the owner answered in their own words. */
  reply?: string;
}

/** One event from /chat/stream. */
export type ChatEvent =
  | { kind: "content"; text: string }
  | { kind: "tool"; tool: ToolEvent }
  | { kind: "approval"; approval: ApprovalRequest }
  | { kind: "approval_resolved"; resolution: ApprovalResolution }
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

export function parseEvent(payload: string): ChatEvent | null {
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
  if (parsed.approval) return { kind: "approval", approval: parsed.approval as ApprovalRequest };
  if (parsed.approval_resolved) return { kind: "approval_resolved", resolution: parsed.approval_resolved as ApprovalResolution };
  return null;
}

/** Answer an approval card with a button. A 404 means the request already ended. */
export async function answerApproval(id: string, decision: ApprovalDecision): Promise<void> {
  await postJson(`/chat/approvals/${encodeURIComponent(id)}`, { decision });
}

/** Answer an approval card in the owner's own words; returns how the reply was read. */
export async function replyToApproval(id: string, text: string): Promise<ApprovalResolution["decision"]> {
  const path = `/chat/approvals/${encodeURIComponent(id)}/reply`;
  return (await postJson<{ decision: ApprovalResolution["decision"] }>(path, { text })).decision;
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
