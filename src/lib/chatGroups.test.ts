import { describe, expect, it } from "vitest";
import { groupChats } from "./chatGroups";
import type { ChatSummary } from "../services/chats";

const at = (...parts: [number, number, number, number?, number?]) => new Date(...parts).getTime() / 1000;

function chat(id: string, updated: number): ChatSummary {
  return { id, title: id, model: "m", created_at: updated, updated_at: updated };
}

describe("groupChats", () => {
  const now = new Date(2026, 9, 19, 9, 0);

  it("buckets by local calendar day, in a fixed order", () => {
    const groups = groupChats(
      [
        chat("today", at(2026, 9, 19, 8, 0)),
        chat("yesterday", at(2026, 9, 18, 23, 59)),
        chat("week", at(2026, 9, 13, 10, 0)),
        chat("older", at(2026, 9, 1)),
      ],
      now,
    );

    expect(groups.map((group) => group.label)).toEqual(["Today", "Yesterday", "Previous 7 days", "Older"]);
    expect(groups.map((group) => group.chats.map((c) => c.id))).toEqual([["today"], ["yesterday"], ["week"], ["older"]]);
  });

  it("omits empty groups", () => {
    const groups = groupChats([chat("a", at(2026, 8, 1)), chat("b", at(2026, 7, 1))], now);

    expect(groups).toEqual([{ label: "Older", chats: [chat("a", at(2026, 8, 1)), chat("b", at(2026, 7, 1))] }]);
  });

  it("treats midnight today as Today and exactly seven days back as Previous 7 days", () => {
    const groups = groupChats([chat("midnight", at(2026, 9, 19, 0, 0)), chat("edge", at(2026, 9, 12, 0, 0))], now);

    expect(groups.map((group) => [group.label, group.chats[0].id])).toEqual([
      ["Today", "midnight"],
      ["Previous 7 days", "edge"],
    ]);
  });
});
