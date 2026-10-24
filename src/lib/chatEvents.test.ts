import { describe, expect, it } from "vitest";
import { parseEvent } from "../services/api";
import type { ApprovalRequest } from "../services/api";
import { ApprovalState, Message, applyEvent, approvalNote, markPendingStopped } from "./chatEvents";

const request = (id: string): ApprovalRequest => ({
  id,
  tool: "filesystem.write_file",
  title: "Write a file",
  summary: "Write 5 characters to a.txt",
  details: { path: "a.txt" },
  irreversible: false,
});

const empty = (): Message => ({ id: "m", role: "assistant", content: "", tools: [], approvals: [] });

const card = (patch: Partial<ApprovalState>): ApprovalState => ({ ...request("a"), status: "resolved", ...patch });

describe("parseEvent", () => {
  it("recognises approval events", () => {
    expect(parseEvent(JSON.stringify({ approval: request("a") }))).toEqual({ kind: "approval", approval: request("a") });
    const resolution = { id: "a", decision: "allow", via: "button" };
    expect(parseEvent(JSON.stringify({ approval_resolved: resolution }))).toEqual({ kind: "approval_resolved", resolution });
  });
});

describe("applyEvent", () => {
  it("adds a pending card, then resolves it by id without touching others", () => {
    let message = applyEvent(empty(), { kind: "approval", approval: request("a") });
    message = applyEvent(message, { kind: "approval", approval: request("b") });
    expect(message.approvals.map((c) => c.status)).toEqual(["pending", "pending"]);

    message = applyEvent(message, { kind: "approval_resolved", resolution: { id: "b", decision: "deny", via: "button" } });
    expect(message.approvals[0].status).toBe("pending");
    expect(message.approvals[1]).toMatchObject({ status: "resolved", resolution: { decision: "deny" } });
  });

  it("replaces the running row with a declined one", () => {
    let message = applyEvent(empty(), { kind: "tool", tool: { name: "test.write", status: "running" } });
    message = applyEvent(message, { kind: "tool", tool: { name: "test.write", status: "declined" } });
    expect(message.tools.map((t) => t.status)).toEqual(["declined"]);
  });
});

describe("markPendingStopped", () => {
  it("stops unanswered cards only", () => {
    const message = {
      ...empty(),
      approvals: [card({ id: "p", status: "pending" }), card({ id: "w", status: "answering" }), card({ id: "r" })],
    };
    expect(markPendingStopped(message).approvals.map((c) => c.status)).toEqual(["stopped", "stopped", "resolved"]);
  });

  it("keeps the same message when nothing is pending", () => {
    const message = { ...empty(), approvals: [card({})] };
    expect(markPendingStopped(message)).toBe(message);
  });
});

describe("approvalNote", () => {
  const resolved = (decision: "allow" | "always" | "deny" | "other", via: "button" | "reply" | "timeout", reply?: string) =>
    approvalNote(card({ resolution: { id: "a", decision, via, reply } }));

  it("says what happened, in the card's words", () => {
    expect(resolved("allow", "button")).toBe("Allowed");
    expect(resolved("allow", "reply", "yes")).toBe("Allowed from your reply");
    expect(resolved("always", "button")).toBe("Always allowed. You can change this in Settings > Permissions");
    expect(resolved("deny", "button")).toBe("Not allowed");
    expect(resolved("deny", "reply", "nah")).toBe("Not allowed from your reply");
    expect(resolved("other", "reply", "check the other folder")).toBe('Not allowed. You said: "check the other folder"');
    expect(resolved("deny", "timeout")).toBe("No answer in 10 minutes, so it wasn't done");
    expect(approvalNote(card({ status: "stopped" }))).toBe("Stopped before you answered");
  });
});
