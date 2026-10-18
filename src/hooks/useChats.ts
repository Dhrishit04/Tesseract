import { useCallback, useEffect, useRef, useState } from "react";
import { ChatSummary, createChat, deleteChat, listChats, updateChat } from "../services/chats";

export type ChatListStatus = "loading" | "ready" | "error";

const SEARCH_DEBOUNCE_MS = 200;

/** The sidebar's list of saved chats. The active chat itself comes from the app route. */
export function useChats() {
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [status, setStatus] = useState<ChatListStatus>("loading");
  const [query, setQuery] = useState("");
  const queryRef = useRef("");

  const refresh = useCallback(async () => {
    try {
      const next = await listChats(queryRef.current);
      setChats(next);
      setStatus("ready");
      return next;
    } catch {
      setStatus("error");
      return null;
    }
  }, []);

  useEffect(() => {
    queryRef.current = query;
    const timer = window.setTimeout(() => void refresh(), query ? SEARCH_DEBOUNCE_MS : 0);
    return () => window.clearTimeout(timer);
  }, [query, refresh]);

  const create = useCallback(
    async (model: string) => {
      const chat = await createChat(model);
      await refresh();
      return chat.id;
    },
    [refresh],
  );

  const rename = useCallback(
    async (id: string, title: string) => {
      await updateChat(id, { title });
      await refresh();
    },
    [refresh],
  );

  /** Deletes a chat and returns the chats that remain (unfiltered, newest first). */
  const remove = useCallback(
    async (id: string) => {
      await deleteChat(id);
      await refresh();
      return listChats();
    },
    [refresh],
  );

  const retry = useCallback(() => {
    setStatus("loading");
    void refresh();
  }, [refresh]);

  return { chats, status, query, setQuery, refresh, retry, create, rename, remove };
}

export type ChatsState = ReturnType<typeof useChats>;
