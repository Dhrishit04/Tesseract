// How streamed chat events change a message, kept free of React so it can be tested directly.
import type { ApprovalRequest, ApprovalResolution, ChatEvent, ToolEvent } from "../services/api";

export interface ToolActivity extends ToolEvent {
  id: string;
}

export interface ApprovalState extends ApprovalRequest {
  status: "pending" | "answering" | "resolved" | "stopped";
  resolution?: ApprovalResolution;
  error?: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  tools: ToolActivity[];
  approvals: ApprovalState[];
  error?: { message: string; code?: string };
  stopped?: boolean;
}

export function applyEvent(message: Message, event: ChatEvent): Message {
  switch (event.kind) {
    case "content":
      return { ...message, content: message.content + event.text };
    case "error":
      return { ...message, error: { message: event.message, code: event.code } };
    case "approval":
      return { ...message, approvals: [...message.approvals, { ...event.approval, status: "pending" }] };
    case "approval_resolved":
      return {
        ...message,
        approvals: message.approvals.map((card) =>
          card.id === event.resolution.id ? { ...card, status: "resolved", resolution: event.resolution, error: undefined } : card,
        ),
      };
    case "tool": {
      const tools = [...message.tools];
      const running = tools.findIndex((tool) => tool.name === event.tool.name && tool.status === "running");
      if (event.tool.status !== "running" && running >= 0) {
        tools[running] = { ...tools[running], ...event.tool };
      } else {
        tools.push({ ...event.tool, id: crypto.randomUUID() });
      }
      return { ...message, tools };
    }
  }
}

const unanswered = (card: ApprovalState) => card.status === "pending" || card.status === "answering";

/** The turn ended (Stop, closed stream): cards nobody answered can no longer be answered. */
export function markPendingStopped(message: Message): Message {
  if (!message.approvals.some(unanswered)) return message;
  return { ...message, approvals: message.approvals.map((card) => (unanswered(card) ? { ...card, status: "stopped" } : card)) };
}

/** The line a card shows once it is settled. */
export function approvalNote(card: ApprovalState): string {
  if (card.status === "stopped") return "Stopped before you answered";
  const resolution = card.resolution;
  if (!resolution) return "";
  if (resolution.via === "timeout") return "No answer in 10 minutes, so it wasn't done";
  const fromReply = resolution.via === "reply";
  switch (resolution.decision) {
    case "allow":
      return fromReply ? "Allowed from your reply" : "Allowed";
    case "always":
      return "Always allowed. You can change this in Settings > Permissions";
    case "deny":
      return fromReply ? "Not allowed from your reply" : "Not allowed";
    case "other":
      return `Not allowed. You said: "${resolution.reply ?? ""}"`;
  }
}
