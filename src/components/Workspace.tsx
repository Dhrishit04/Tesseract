import { AnimatePresence, motion } from "motion/react";
import { FormEvent, KeyboardEvent, useRef, useState } from "react";
import type { ChatState } from "../hooks/useChat";
import type { SidecarState } from "../hooks/useSidecarStatus";
import { SPRING_PRESS, calmVariants, chatVariants, useReducedMotionPreference } from "../lib/motion";
import { MessageView } from "./MessageView";
import { ModelPicker } from "./ModelPicker";
import { SmoothScroll } from "./SmoothScroll";
import { TesseractMark } from "./TesseractMark";
import { VoiceControls } from "./VoiceControls";

const SUGGESTIONS = [
  "List the files in my working folder",
  "What is on my calendar tomorrow?",
  "Open Notepad and type a reminder to call the bank",
];

function composerHint(answering: boolean, busyElsewhere: boolean): string {
  if (answering) return "Answer in your own words, or use the buttons above";
  if (busyElsewhere) return "Finishing a reply in another chat";
  return "Enter to send, Shift+Enter for a new line";
}

interface WorkspaceProps {
  chat: ChatState;
  chatId: string | null;
  title: string;
  sidecar: SidecarState;
  onRetrySidecar: () => void;
  onOpenSettings: () => void;
  onOpenSidebar: () => void;
}

export function Workspace({ chat, chatId, title, sidecar, onRetrySidecar, onOpenSettings, onOpenSidebar }: Readonly<WorkspaceProps>) {
  const { messages, loading, loadError, isLoading, busyElsewhere, model, setModel, sendMessage, stop, pendingApproval, answer, reply } = chat;
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const reduced = useReducedMotionPreference();

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    if (!draft.trim()) return;
    if (pendingApproval) {
      reply(draft); // answers the card waiting above
    } else if (isLoading || busyElsewhere) {
      return;
    } else {
      void sendMessage(draft);
    }
    setDraft("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  const applySuggestion = (text: string) => {
    setDraft(text);
    inputRef.current?.focus();
  };

  const lastId = messages[messages.length - 1]?.id;

  let body;
  if (loadError) {
    body = (
      <div className="empty-state" role="alert">
        <h2>This chat can't be opened</h2>
        <p>{loadError}</p>
        <a className="primary-button" href="#/chat/new">Start a new chat</a>
      </div>
    );
  } else if (loading) {
    body = (
      <div className="message" aria-label="Loading conversation">
        {[60, 85, 40].map((width) => <span key={width} className="skeleton skeleton-line" style={{ width: `${width}%` }} />)}
      </div>
    );
  } else if (messages.length === 0) {
    body = (
      <div className="empty-state">
        <TesseractMark size={88} className="empty-mark mark-alive" />
        <h2>What should Tesseract do?</h2>
        <p>
          Ask in plain language. Tesseract can work with your files, open apps, use a browser,
          and read your Gmail and Calendar once you connect them in Settings.
        </p>
        <ul className="suggestions" aria-label="Try one of these">
          {SUGGESTIONS.map((text) => (
            <li key={text}>
              <motion.button type="button" onClick={() => applySuggestion(text)} whileTap={reduced ? undefined : { scale: 0.96 }} transition={SPRING_PRESS}>
                {text}
              </motion.button>
            </li>
          ))}
        </ul>
      </div>
    );
  } else {
    body = messages.map((message) => (
      <MessageView
        key={message.id}
        message={message}
        isStreaming={isLoading && message.id === lastId}
        onOpenSettings={onOpenSettings}
        onAnswer={answer}
      />
    ));
  }

  return (
    <section className="workspace" aria-label="Workspace">
      <header className="workspace-bar">
        <button type="button" className="quiet-button sidebar-open-button" onClick={onOpenSidebar}>Chats</button>
        <h1 className="workspace-title">{title}</h1>
        <div className="workspace-tools">
          {/* Remount after an outage so models load once the sidecar finishes starting. */}
          <ModelPicker key={sidecar === "offline" ? "offline" : "up"} currentModel={model} onModelChange={setModel} autoPick={!chatId} />
          <VoiceControls onTranscription={(text) => void sendMessage(text)} onOpenSettings={onOpenSettings} />
        </div>
      </header>

      {sidecar === "offline" && (
        <div className="banner" role="alert">
          <p>
            The assistant service is not responding. It normally starts with the app. From a
            source checkout you can start it with <code>python sidecar/main.py</code>.
          </p>
          <button type="button" className="text-button" onClick={onRetrySidecar}>Check again</button>
        </div>
      )}

      <SmoothScroll className="conversation" label="Conversation" followKey={messages} restoreKey={chatId ?? "new"} canJump={messages.length > 0}>
        <AnimatePresence mode="wait" initial={false}>
          <motion.div key={chatId ?? "new"} variants={reduced ? calmVariants : chatVariants} initial="initial" animate="animate" exit="exit">
            {body}
          </motion.div>
        </AnimatePresence>
      </SmoothScroll>

      <form className="composer" onSubmit={submit}>
        <label htmlFor="prompt" className="visually-hidden">Message Tesseract</label>
        <textarea
          id="prompt"
          ref={inputRef}
          rows={1}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Talk to Tesseract"
          aria-describedby="composer-hint"
        />
        <div className="composer-actions">
          {pendingApproval && <button type="submit" className="primary-button" disabled={!draft.trim()}>Reply</button>}
          {isLoading ? (
            <button type="button" className={pendingApproval ? "secondary-button" : "primary-button"} onClick={stop}>Stop</button>
          ) : (
            <button type="submit" className="primary-button" disabled={!draft.trim() || busyElsewhere}>Send</button>
          )}
        </div>
        <p id="composer-hint" className="composer-hint">
          {composerHint(Boolean(pendingApproval), busyElsewhere)}
        </p>
      </form>
    </section>
  );
}
