import type { ChatSummary } from "../services/chats";

export type ChatGroupLabel = "Today" | "Yesterday" | "Previous 7 days" | "Older";

export interface ChatGroup {
  label: ChatGroupLabel;
  chats: ChatSummary[];
}

const DAY_MS = 24 * 60 * 60 * 1000;
const ORDER: ChatGroupLabel[] = ["Today", "Yesterday", "Previous 7 days", "Older"];

/** Sidebar sections by local calendar day of the last activity. Input order is kept within a group. */
export function groupChats(chats: ChatSummary[], now: Date): ChatGroup[] {
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const startOfYesterday = startOfToday - DAY_MS;
  const startOfWeek = startOfToday - 7 * DAY_MS;

  const buckets = new Map<ChatGroupLabel, ChatSummary[]>(ORDER.map((label) => [label, []]));
  for (const chat of chats) {
    const time = chat.updated_at * 1000;
    let label: ChatGroupLabel = "Older";
    if (time >= startOfToday) label = "Today";
    else if (time >= startOfYesterday) label = "Yesterday";
    else if (time >= startOfWeek) label = "Previous 7 days";
    buckets.get(label)?.push(chat);
  }
  return ORDER.filter((label) => buckets.get(label)?.length).map((label) => ({ label, chats: buckets.get(label) ?? [] }));
}
