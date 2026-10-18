// Saved conversations, stored by the sidecar in ~/.tesseract/tesseract.db.

import { sidecarJson } from "../lib/sidecar";
import type { ToolEvent } from "./api";

/** Timestamps are Unix seconds. */
export interface ChatSummary {
  id: string;
  title: string;
  model: string;
  created_at: number;
  updated_at: number;
}

export interface StoredMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  tools: ToolEvent[];
  created_at: number;
}

export type ChatDetail = ChatSummary & { messages: StoredMessage[] };

export async function listChats(query = ""): Promise<ChatSummary[]> {
  const suffix = query.trim() ? `?q=${encodeURIComponent(query.trim())}` : "";
  return (await sidecarJson<{ chats: ChatSummary[] }>(`/chats${suffix}`)).chats;
}

export function createChat(model: string): Promise<ChatSummary> {
  return sidecarJson<ChatSummary>("/chats", { method: "POST", body: JSON.stringify({ model }) });
}

export function getChat(id: string): Promise<ChatDetail> {
  return sidecarJson<ChatDetail>(`/chats/${encodeURIComponent(id)}`);
}

export function updateChat(id: string, patch: { title?: string; model?: string }): Promise<ChatSummary> {
  return sidecarJson<ChatSummary>(`/chats/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(patch) });
}

export async function deleteChat(id: string): Promise<void> {
  await sidecarJson(`/chats/${encodeURIComponent(id)}`, { method: "DELETE" });
}
