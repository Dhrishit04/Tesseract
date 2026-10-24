import { useCallback, useEffect, useRef, useState } from "react";
import { ApprovalDecision, ChatEvent, answerApproval, replyToApproval, streamChat } from "../services/api";
import { StoredMessage, getChat, updateChat } from "../services/chats";
import { DEFAULT_MODEL, getSettings } from "../services/settings";
import { ApprovalState, Message, applyEvent, markPendingStopped } from "../lib/chatEvents";
import { SidecarError, errorMessage } from "../lib/sidecar";

export type { Message, ToolActivity } from "../lib/chatEvents";

const APPROVAL_ENDED = "This request has already ended.";

function fromStored(message: StoredMessage): Message {
  return {
    id: message.id,
    role: message.role,
    content: message.content,
    tools: message.tools.map((tool) => ({ ...tool, id: crypto.randomUUID() })),
    approvals: [],
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
          { id: crypto.randomUUID(), role: "user", content, tools: [], approvals: [] },
          { id: assistantId, role: "assistant", content: "", tools: [], approvals: [] },
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
      // The message events go to; a typed approval reply starts a new one, as the server saves it.
      let targetId = assistantId;
      const onEvent = (event: ChatEvent) => {
        updateMessage(id, targetId, (message) => applyEvent(message, event));
        const reply = event.kind === "approval_resolved" ? event.resolution.reply : undefined;
        if (reply === undefined) return;
        targetId = crypto.randomUUID();
        setMessagesByChat((all) => ({
          ...all,
          [id]: [
            ...(all[id] ?? []),
            { id: crypto.randomUUID(), role: "user", content: reply, tools: [], approvals: [] },
            { id: targetId, role: "assistant", content: "", tools: [], approvals: [] },
          ],
        }));
      };
      try {
        await streamChat({ message: content, model, chat_id: id }, onEvent, controller.signal);
      } catch (error) {
        if (controller.signal.aborted) {
          updateMessage(id, targetId, (message) => ({ ...message, stopped: true }));
        } else {
          updateMessage(id, targetId, (message) => ({ ...message, error: { message: errorMessage(error), code: "offline" } }));
        }
      } finally {
        // However the turn ended, nothing is waiting on its cards any more.
        setMessagesByChat((all) => (all[id] ? { ...all, [id]: all[id].map(markPendingStopped) } : all));
        abortRef.current = null;
        setStreamingChat(null);
        onTurnSaved();
      }
    },
    [chatId, ensureChat, model, onCreated, onTurnSaved, updateMessage],
  );

  const stop = useCallback(() => abortRef.current?.controller.abort(), []);

  const updateCard = useCallback((id: string, approvalId: string, change: (card: ApprovalState) => ApprovalState) => {
    setMessagesByChat((all) => ({
      ...all,
      [id]: (all[id] ?? []).map((message) =>
        message.approvals.some((card) => card.id === approvalId)
          ? { ...message, approvals: message.approvals.map((card) => (card.id === approvalId ? change(card) : card)) }
          : message,
      ),
    }));
  }, []);

  /** Mark a card as being answered; the approval_resolved event settles it, a failure puts it back. */
  const settle = useCallback(
    (id: string, approvalId: string, request: () => Promise<unknown>) => {
      updateCard(id, approvalId, (card) => ({ ...card, status: "answering", error: undefined }));
      request().catch((error) =>
        updateCard(id, approvalId, (card) => {
          if (card.status !== "answering") return card; // the stream already settled it
          if (error instanceof SidecarError && error.status === 404) return { ...card, status: "stopped", error: APPROVAL_ENDED };
          return { ...card, status: "pending", error: errorMessage(error) };
        }),
      );
    },
    [updateCard],
  );

  const messages = chatId ? messagesByChat[chatId] ?? [] : [];
  const pendingApproval =
    messages.flatMap((message) => message.approvals).find((card) => card.status === "pending") ?? null;

  const answer = useCallback(
    (approvalId: string, decision: ApprovalDecision) => {
      if (chatId) settle(chatId, approvalId, () => answerApproval(approvalId, decision));
    },
    [chatId, settle],
  );

  const pendingId = pendingApproval?.id;
  const reply = useCallback(
    (text: string) => {
      const content = text.trim();
      if (chatId && pendingId && content) settle(chatId, pendingId, () => replyToApproval(pendingId, content));
    },
    [chatId, pendingId, settle],
  );

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
    messages,
    loading: Boolean(chatId) && !messagesByChat[chatId ?? ""] && !loadError,
    loadError,
    isLoading: streamingChat !== null && streamingChat === chatId,
    busyElsewhere: streamingChat !== null && streamingChat !== chatId,
    model,
    setModel,
    sendMessage,
    stop,
    pendingApproval,
    answer,
    reply,
    discard,
  };
}

export type ChatState = ReturnType<typeof useChat>;
