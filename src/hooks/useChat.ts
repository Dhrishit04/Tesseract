import { useCallback, useEffect, useRef, useState } from "react";
import { ChatEvent, ToolEvent, streamChat } from "../services/api";
import { StoredMessage, getChat, updateChat } from "../services/chats";
import { DEFAULT_MODEL, getSettings } from "../services/settings";
import { errorMessage } from "../lib/sidecar";

export interface ToolActivity extends ToolEvent {
  id: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  tools: ToolActivity[];
  error?: { message: string; code?: string };
  stopped?: boolean;
}

function applyEvent(message: Message, event: ChatEvent): Message {
  switch (event.kind) {
    case "content":
      return { ...message, content: message.content + event.text };
    case "error":
      return { ...message, error: { message: event.message, code: event.code } };
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

function fromStored(message: StoredMessage): Message {
  return {
    id: message.id,
    role: message.role,
    content: message.content,
    tools: message.tools.map((tool) => ({ ...tool, id: crypto.randomUUID() })),
  };
}

interface UseChatOptions {
  /** Creates the chat on first send when the route is a new, unsaved chat. */
  ensureChat: (model: string) => Promise<string>;
  /** Called once the new chat's first messages are in place, so the route can switch to it. */
  onCreated: (id: string) => void;
  /** Called after each turn finishes so the sidebar can refresh titles and order. */
  onTurnSaved: () => void;
}

/**
 * Messages of the open chat. Messages are kept per chat id, so an answer that is still
 * streaming into one chat keeps arriving while another chat is open.
 */
export function useChat(chatId: string | null, { ensureChat, onCreated, onTurnSaved }: UseChatOptions) {
  const [messagesByChat, setMessagesByChat] = useState<Record<string, Message[]>>({});
  const [modelsByChat, setModelsByChat] = useState<Record<string, string>>({});
  const [defaultModel, setDefaultModel] = useState(DEFAULT_MODEL);
  const [streamingChat, setStreamingChat] = useState<string | null>(null);
  const [loadError, setLoadError] = useState("");
  const abortRef = useRef<{ chatId: string; controller: AbortController } | null>(null);
  const [draftModel, setDraftModel] = useState<string | null>(null);

  useEffect(() => {
    getSettings()
      .then((settings) => settings.default_model && setDefaultModel(settings.default_model))
      .catch(() => undefined); // keep the built-in default when settings are unreadable
  }, []);

  // Chats whose messages this hook already holds (loaded, or created and streaming here).
  // A ref, not state: it must be updated before the route switches to a new chat.
  const heldChats = useRef(new Set<string>());

  // Load the chat from disk unless it is already in memory (e.g. still streaming).
  useEffect(() => {
    setLoadError("");
    if (!chatId || heldChats.current.has(chatId)) return;
    let cancelled = false;
    getChat(chatId)
      .then((chat) => {
        if (cancelled) return;
        heldChats.current.add(chatId);
        // Never overwrite messages that arrived while the request was in flight.
        setMessagesByChat((all) => (all[chatId] ? all : { ...all, [chatId]: chat.messages.map(fromStored) }));
        if (chat.model) setModelsByChat((all) => ({ [chatId]: chat.model, ...all }));
      })
      .catch((error) => !cancelled && setLoadError(errorMessage(error)));
    return () => {
      cancelled = true;
    };
  }, [chatId]);

  const model = (chatId && modelsByChat[chatId]) || draftModel || defaultModel;

  const setModel = useCallback(
    (next: string) => {
      if (!chatId) {
        setDraftModel(next);
        return;
      }
      setModelsByChat((all) => ({ ...all, [chatId]: next }));
      updateChat(chatId, { model: next }).catch(() => undefined); // the choice still applies to this session
    },
    [chatId],
  );

  const updateMessage = useCallback((id: string, messageId: string, change: (message: Message) => Message) => {
    setMessagesByChat((all) => ({
      ...all,
      [id]: (all[id] ?? []).map((message) => (message.id === messageId ? change(message) : message)),
    }));
  }, []);

  const sendMessage = useCallback(
    async (text: string) => {
      const content = text.trim();
      if (!content || abortRef.current) return;

      const id = chatId ?? (await ensureChat(model));
      heldChats.current.add(id);
      const assistantId = crypto.randomUUID();
      setMessagesByChat((all) => ({
        ...all,
        [id]: [
          ...(all[id] ?? []),
          { id: crypto.randomUUID(), role: "user", content, tools: [] },
          { id: assistantId, role: "assistant", content: "", tools: [] },
        ],
      }));
      if (!chatId) {
        setModelsByChat((all) => ({ ...all, [id]: model }));
        setDraftModel(null);
        onCreated(id);
      }

      const controller = new AbortController();
      abortRef.current = { chatId: id, controller };
      setStreamingChat(id);
      try {
        await streamChat(
          { message: content, model, chat_id: id },
          (event) => updateMessage(id, assistantId, (message) => applyEvent(message, event)),
          controller.signal,
        );
      } catch (error) {
        if (controller.signal.aborted) {
          updateMessage(id, assistantId, (message) => ({ ...message, stopped: true }));
        } else {
          updateMessage(id, assistantId, (message) => ({ ...message, error: { message: errorMessage(error), code: "offline" } }));
        }
      } finally {
        abortRef.current = null;
        setStreamingChat(null);
        onTurnSaved();
      }
    },
    [chatId, ensureChat, model, onCreated, onTurnSaved, updateMessage],
  );

  const stop = useCallback(() => abortRef.current?.controller.abort(), []);

  /** Stop and forget a chat that was deleted. */
  const discard = useCallback((id: string) => {
    if (abortRef.current?.chatId === id) abortRef.current.controller.abort();
    heldChats.current.delete(id);
    setMessagesByChat((all) => {
      const rest = { ...all };
      delete rest[id];
      return rest;
    });
  }, []);

  return {
    messages: chatId ? messagesByChat[chatId] ?? [] : [],
    loading: Boolean(chatId) && !messagesByChat[chatId ?? ""] && !loadError,
    loadError,
    isLoading: streamingChat !== null && streamingChat === chatId,
    busyElsewhere: streamingChat !== null && streamingChat !== chatId,
    model,
    setModel,
    sendMessage,
    stop,
    discard,
  };
}

export type ChatState = ReturnType<typeof useChat>;
