import { memo, MouseEvent } from "react";
import Markdown, { Components } from "react-markdown";
import { Message, ToolActivity } from "../hooks/useChat";
import { toolLabel } from "../lib/capabilities";
import { openExternal } from "../lib/sidecar";

// Links in answers open in the system browser; navigating the app webview would lose the session.
const markdownComponents: Components = {
  a: ({ href, children }) => (
    <a
      href={href}
      onClick={(event: MouseEvent<HTMLAnchorElement>) => {
        event.preventDefault();
        if (href) void openExternal(href);
      }}
    >
      {children}
    </a>
  ),
};

const STATUS_TEXT: Record<ToolActivity["status"], string> = {
  running: "Working",
  done: "Done",
  error: "Failed",
};

function ToolRow({ tool }: Readonly<{ tool: ToolActivity }>) {
  return (
    <li className={`tool-row tool-${tool.status}`} title={tool.name}>
      <span className="tool-label">{toolLabel(tool.name)}</span>
      <span className="tool-status">{STATUS_TEXT[tool.status]}</span>
      {tool.error && <p className="tool-error">{tool.error}</p>}
      {tool.preview && <img className="tool-preview" src={tool.preview} alt={`Screenshot from: ${toolLabel(tool.name)}`} />}
    </li>
  );
}

interface MessageViewProps {
  message: Message;
  isStreaming: boolean;
  onOpenSettings: () => void;
}

function MessageViewInner({ message, isStreaming, onOpenSettings }: Readonly<MessageViewProps>) {
  if (message.role === "user") {
    return (
      <article className="message message-user" aria-label="You said">
        <p>{message.content}</p>
      </article>
    );
  }

  const waiting = isStreaming && !message.content && message.tools.length === 0 && !message.error;
  return (
    <article className="message message-assistant" aria-label="Tesseract replied" aria-busy={isStreaming}>
      {message.tools.length > 0 && (
        <ul className="tool-log" aria-label="What Tesseract did">
          {message.tools.map((tool) => <ToolRow key={tool.id} tool={tool} />)}
        </ul>
      )}
      {waiting && <p className="thinking">Thinking</p>}
      {message.content && (
        <div className="markdown">
          <Markdown components={markdownComponents}>{message.content}</Markdown>
        </div>
      )}
      {message.stopped && <p className="message-note">Stopped before finishing.</p>}
      {message.error && (
        <div className="inline-error" role="alert">
          <p>{message.error.message}</p>
          {message.error.code === "no_api_key" && (
            <button type="button" className="text-button" onClick={onOpenSettings}>
              Add API key
            </button>
          )}
        </div>
      )}
    </article>
  );
}

export const MessageView = memo(MessageViewInner);
