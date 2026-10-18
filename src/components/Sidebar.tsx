import { motion } from "motion/react";
import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import type { ChatListStatus } from "../hooks/useChats";
import type { SidecarState } from "../hooks/useSidecarStatus";
import { groupChats } from "../lib/chatGroups";
import { SPRING_PRESS, useReducedMotionPreference } from "../lib/motion";
import type { ChatSummary } from "../services/chats";
import { TesseractMark } from "./TesseractMark";

const SIDECAR_TEXT: Record<SidecarState, string> = {
  checking: "Connecting",
  online: "Assistant ready",
  offline: "Assistant offline",
};

interface SidebarProps {
  chats: ChatSummary[];
  status: ChatListStatus;
  activeId: string | null;
  settingsOpen: boolean;
  query: string;
  sidecar: SidecarState;
  open: boolean;
  onQuery: (query: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onRetry: () => void;
  onClose: () => void;
}

type RowMode = "idle" | "menu" | "rename" | "confirm";

interface ChatRowProps {
  chat: ChatSummary;
  active: boolean;
  onRename: (title: string) => Promise<void>;
  onDelete: () => Promise<void>;
}

function ChatRow({ chat, active, onRename, onDelete }: Readonly<ChatRowProps>) {
  const [mode, setMode] = useState<RowMode>("idle");
  const [title, setTitle] = useState(chat.title);
  const [error, setError] = useState("");
  const reduced = useReducedMotionPreference();
  const menuRef = useRef<HTMLDivElement>(null);
  const renameRef = useRef<HTMLInputElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const label = chat.title || "New chat";

  // Move focus to whatever the person just asked for (the title field or the delete confirmation).
  useEffect(() => {
    if (mode === "rename") renameRef.current?.select();
    if (mode === "confirm") confirmRef.current?.focus();
  }, [mode]);

  useEffect(() => {
    if (mode !== "menu") return;
    menuRef.current?.querySelector<HTMLButtonElement>("[role=menuitem]")?.focus();
    const close = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMode("idle");
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [mode]);

  const run = async (action: () => Promise<void>) => {
    setError("");
    try {
      await action();
      setMode("idle");
    } catch {
      setError("That didn't work. Check that the assistant is running.");
    }
  };

  const submitRename = (event: FormEvent) => {
    event.preventDefault();
    const next = title.trim();
    if (!next || next === chat.title) {
      setMode("idle");
      return;
    }
    void run(() => onRename(next));
  };

  const onMenuKey = (event: KeyboardEvent) => {
    if (event.key === "Escape") setMode("idle");
  };

  if (mode === "rename") {
    return (
      <li className="chat-row chat-row-editing">
        <form onSubmit={submitRename}>
          <label className="visually-hidden" htmlFor={`rename-${chat.id}`}>Chat title</label>
          <input
            id={`rename-${chat.id}`}
            ref={renameRef}
            className="chat-rename"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            onKeyDown={(event) => event.key === "Escape" && setMode("idle")}
            onBlur={submitRename}
          />
        </form>
        {error && <p className="field-error">{error}</p>}
      </li>
    );
  }

  return (
    <li className="chat-row">
      <motion.a
        href={`#/chat/${chat.id}`}
        className="chat-link"
        aria-current={active ? "page" : undefined}
        whileTap={reduced ? undefined : { scale: 0.97 }}
        transition={SPRING_PRESS}
      >
        {active && <motion.span className="chat-link-highlight" layoutId={reduced ? undefined : "active-chat"} transition={SPRING_PRESS} />}
        <span className="chat-link-title">{label}</span>
      </motion.a>
      <button
        type="button"
        className="chat-more"
        aria-label="More options"
        aria-haspopup="menu"
        aria-expanded={mode === "menu"}
        onClick={() => setMode(mode === "menu" ? "idle" : "menu")}
      >
        <span aria-hidden="true">···</span>
      </button>
      {mode === "menu" && (
        <div ref={menuRef} className="chat-menu" role="menu" aria-label={`Options for ${label}`} onKeyDown={onMenuKey} tabIndex={-1}>
          <button type="button" role="menuitem" onClick={() => { setTitle(chat.title); setMode("rename"); }}>Rename</button>
          <button type="button" role="menuitem" className="chat-menu-danger" onClick={() => setMode("confirm")}>Delete</button>
        </div>
      )}
      {mode === "confirm" && (
        <div className="chat-confirm" role="group" aria-label={`Delete ${label}?`}>
          <span>Delete this chat?</span>
          <button type="button" ref={confirmRef} className="danger-button" onClick={() => void run(onDelete)}>Delete chat</button>
          <button type="button" className="quiet-button" onClick={() => setMode("idle")}>Keep</button>
        </div>
      )}
      {error && <p className="field-error">{error}</p>}
    </li>
  );
}

function ChatList({ chats, status, activeId, query, onRename, onDelete, onRetry }: Readonly<Pick<SidebarProps,
  "chats" | "status" | "activeId" | "query" | "onRename" | "onDelete" | "onRetry">>) {
  if (status === "error") {
    return (
      <div className="sidebar-message" role="alert">
        <p>Couldn't load chats</p>
        <button type="button" className="text-button" onClick={onRetry}>Check again</button>
      </div>
    );
  }
  if (status === "loading") {
    return (
      <div className="sidebar-message" aria-label="Loading chats">
        {[70, 55, 80].map((width) => <span key={width} className="skeleton skeleton-line" style={{ width: `${width}%` }} />)}
      </div>
    );
  }
  if (chats.length === 0) {
    return <p className="sidebar-message">{query ? "No chats match" : "No chats yet"}</p>;
  }
  return (
    <>
      {groupChats(chats, new Date()).map((group) => (
        <section key={group.label} className="chat-group" aria-labelledby={`group-${group.label}`}>
          <h2 id={`group-${group.label}`} className="chat-group-title">{group.label}</h2>
          <ul>
            {group.chats.map((chat) => (
              <ChatRow
                key={chat.id}
                chat={chat}
                active={chat.id === activeId}
                onRename={(title) => onRename(chat.id, title)}
                onDelete={() => onDelete(chat.id)}
              />
            ))}
          </ul>
        </section>
      ))}
    </>
  );
}

export function Sidebar(props: Readonly<SidebarProps>) {
  const { query, sidecar, open, settingsOpen, onQuery, onNew, onClose } = props;
  const reduced = useReducedMotionPreference();

  return (
    <aside className={`sidebar${open ? " sidebar-open" : ""}`} aria-label="Sidebar">
      <div className="sidebar-top">
        <a className="brand" href="#/chat/new" aria-label="Tesseract, new chat">
          <TesseractMark size={22} className="brand-mark" />
          <span>Tesseract</span>
        </a>
        <button type="button" className="quiet-button sidebar-close" onClick={onClose}>Close</button>
      </div>
      <motion.button
        type="button"
        className="new-chat"
        onClick={onNew}
        whileTap={reduced ? undefined : { scale: 0.96 }}
        transition={SPRING_PRESS}
      >
        <span aria-hidden="true">+</span> New chat
      </motion.button>
      <div className="sidebar-search">
        <label className="visually-hidden" htmlFor="chat-search">Search chats</label>
        <input id="chat-search" type="search" placeholder="Search chats" value={query} onChange={(event) => onQuery(event.target.value)} />
      </div>
      <nav className="sidebar-chats" aria-label="Chats">
        <ChatList {...props} />
      </nav>
      <div className="sidebar-foot">
        <a href="#/settings" className="sidebar-link" aria-current={settingsOpen ? "page" : undefined}>Settings</a>
        <output className={`sidecar-status sidecar-${sidecar}`}>
          <span className="status-dot" aria-hidden="true" />
          {SIDECAR_TEXT[sidecar]}
        </output>
      </div>
    </aside>
  );
}
