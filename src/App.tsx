import { FormEvent, useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { useChat } from "./hooks/useChat";
import { ModelPicker } from "./components/ModelPicker";
import { SettingsPanel } from "./components/SettingsPanel";
import { VoiceIndicator } from "./components/VoiceIndicator";

type View = "home" | "privacy" | "terms" | "settings";

function ArrowUpRight() {
  return <span aria-hidden="true" className="arrow">↗</span>;
}

function App() {
  const { messages, isLoading, currentModel, setCurrentModel, sendMessage, clearMessages } = useChat();
  const [view, setView] = useState<View>("home");
  const [input, setInput] = useState("");
  const [email, setEmail] = useState("");
  const [formMessage, setFormMessage] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);

  useEffect(() => {
    if (!("__TAURI_INTERNALS__" in window)) return;
    let unlisten: (() => void) | undefined;
    listen("open-settings", () => {
      setView("settings");
      setSidebarOpen(true);
    }).then((fn) => {
      unlisten = fn;
    });
    return () => unlisten?.();
  }, []);

  const submitPrompt = (event?: FormEvent) => {
    event?.preventDefault();
    if (!input.trim() || isLoading) return;
    sendMessage(input.trim());
    setInput("");
  };

  const submitEmail = (event: FormEvent) => {
    event.preventDefault();
    if (!email.trim() || !email.includes("@")) {
      setFormMessage("Enter a valid email address.");
      return;
    }
    setFormMessage("Thanks. We’ll be in touch when Tesseract is ready.");
    setEmail("");
  };

  const goTo = (nextView: View, anchor?: string) => {
    setView(nextView);
    setSidebarOpen(false);
    if (anchor) window.setTimeout(() => document.getElementById(anchor)?.scrollIntoView({ behavior: "smooth" }), 0);
  };

  if (view === "settings") {
    return (
      <div className="app-shell">
        <header className="app-header">
          <button className="brand" onClick={() => goTo("home")} aria-label="Back to Tesseract home">TESSERACT</button>
          <button className="text-button" onClick={() => goTo("home")}>Back to home</button>
        </header>
        <main className="settings-wrap"><SettingsPanel /></main>
      </div>
    );
  }

  if (view === "privacy" || view === "terms") {
    const isPrivacy = view === "privacy";
    return (
      <div className="legal-page">
        <header className="site-header">
          <button className="brand" onClick={() => goTo("home")}>TESSERACT</button>
          <button className="text-button" onClick={() => goTo("home")}>Back to home</button>
        </header>
        <main className="legal-content">
          <p className="eyebrow">DRAFT FOR REVIEW · 22 SEPTEMBER 2026</p>
          <h1>{isPrivacy ? "Privacy Policy" : "Terms of Service"}</h1>
          <p className="legal-lede">This {isPrivacy ? "privacy policy" : "terms page"} is a draft for review. Tesseract is an early product and these details should be completed before public launch.</p>
          <section>
            <h2>{isPrivacy ? "What we currently collect" : "Using Tesseract"}</h2>
            <p>{isPrivacy
              ? "The desktop app sends prompts, selected model details, and conversation history to the configured AI provider so it can respond. We do not currently operate a separate account system or publish a retention schedule."
              : "Tesseract is provided as an experimental desktop assistant. You are responsible for the prompts and files you choose to send to an AI provider, and for checking generated output before relying on it."}</p>
          </section>
          <section>
            <h2>{isPrivacy ? "Third-party providers" : "Availability and changes"}</h2>
            <p>{isPrivacy
              ? "Your configured model provider may process data under its own policies. Review that provider’s terms before using sensitive information. This draft will name the active providers and contact address before launch."
              : "Features may change while Tesseract is in development. We will publish an updated version of this page with support, warranty, and contact details before release."}</p>
          </section>
          <p className="legal-note">Draft status: review with the product owner and legal counsel before publishing.</p>
        </main>
      </div>
    );
  }

  return (
    <div className="site">
      <header className="site-header">
        <button className="brand" onClick={() => goTo("home")} aria-label="Tesseract home">TESSERACT</button>
        <nav className={sidebarOpen ? "nav-open" : ""} aria-label="Main navigation">
          <button onClick={() => goTo("home", "product")}>Product</button>
          <button onClick={() => goTo("home", "principles")}>Principles</button>
          <button onClick={() => goTo("home", "contact")}>Stay in the loop</button>
          <button className="nav-cta" onClick={() => goTo("home", "try")}>Try the demo <ArrowUpRight /></button>
        </nav>
        <button className="menu-button" onClick={() => setSidebarOpen(!sidebarOpen)} aria-expanded={sidebarOpen} aria-label="Toggle navigation">
          <span /><span />
        </button>
      </header>

      <main>
        <section className="hero">
          <div className="hero-copy">
            <p className="eyebrow">A focused AI workspace for your desktop</p>
            <h1>Make room for<br /><em>better thinking.</em></h1>
            <p className="hero-lede">Tesseract brings capable AI, voice, and the tools you use every day into one quiet place.</p>
            <button className="primary-button" onClick={() => goTo("home", "try")}>Open the workspace <ArrowUpRight /></button>
          </div>
          <div className="hero-mark" aria-hidden="true">
            <div className="mark-ring ring-one" /><div className="mark-ring ring-two" /><div className="mark-core" />
            <span>01 / 03</span>
          </div>
          <div className="hero-foot"><span>Built for considered work</span><span>Scroll to explore ↓</span></div>
        </section>

        <section className="intro-section" id="product">
          <div className="section-label">01 / The workspace</div>
          <div className="intro-grid">
            <h2>One clear surface<br />for complex work.</h2>
            <div><p>Good tools stay out of the way. Tesseract keeps your conversation, chosen model, and voice controls close at hand, without turning your desktop into a control room.</p><p>Ask a question, shape an idea, or pick up exactly where you left off.</p></div>
          </div>
        </section>

        <section className="product-section" id="try">
          <div className="product-heading"><p className="eyebrow">Live workspace</p><h2>Try it with a real prompt.</h2><p>Responses use your configured local connection. Nothing here is simulated.</p></div>
          <div className="workspace-window">
            <div className="workspace-topbar"><div className="window-dots"><i /><i /><i /></div><span>Tesseract / workspace</span><span className="status"><b /> Ready</span></div>
            <div className="workspace-body">
              <aside className="workspace-side"><div className="side-brand">TESSERACT</div><button className="side-active"><span>New conversation</span></button><button onClick={() => goTo("settings")}><span>Settings</span></button><button onClick={clearMessages}><span>Clear chat</span></button><small>LOCAL WORKSPACE<br />CONNECTED</small></aside>
              <div className="chat-panel">
                <div className="chat-toolbar"><span>Conversation</span><div className="toolbar-actions"><ModelPicker currentModel={currentModel} onModelChange={setCurrentModel} /><VoiceIndicator onTranscription={(text) => text.trim() && sendMessage(text.trim())} /></div></div>
                <div className="messages">
                  {messages.length === 0 ? <div className="empty-chat"><span className="empty-glyph">T</span><h3>What are you working on?</h3><p>Ask Tesseract to clarify, draft, or explore an idea.</p><div className="prompt-chips"><button onClick={() => setInput("Help me think through a difficult decision")}>Think through a decision</button><button onClick={() => setInput("Turn these notes into a clear outline")}>Shape some notes</button></div></div> : messages.map((message) => <div className={`message ${message.role}`} key={message.id}><span className="message-label">{message.role === "user" ? "YOU" : "TESSERACT"}</span><p>{message.content || "Thinking..."}</p></div>)}
                  {isLoading && messages.length > 0 && <div className="typing"><i /><i /><i /></div>}
                </div>
                <form className="composer" onSubmit={submitPrompt}><input aria-label="Message Tesseract" value={input} onChange={(event) => setInput(event.target.value)} placeholder="Ask anything..." disabled={isLoading} /><button type="submit" disabled={isLoading || !input.trim()} aria-label="Send message">↑</button></form>
              </div>
            </div>
          </div>
        </section>

        <section className="principles-section" id="principles">
          <div className="section-label">02 / The point of view</div>
          <div className="principles-intro"><h2>Less interface.<br /><em>More intention.</em></h2><p>Tesseract is designed for the moments when you need to stay with a thought. It is responsive when you ask, quiet when you do not, and flexible enough to fit the way you already work.</p></div>
          <div className="principle-list"><div><span>01</span><h3>Clarity over clutter</h3><p>Every control earns its place. The conversation stays central.</p></div><div><span>02</span><h3>Your choice of intelligence</h3><p>Switch between connected models without rebuilding your workflow.</p></div><div><span>03</span><h3>Voice when it helps</h3><p>Move from thought to words and back again with hands-free controls.</p></div></div>
        </section>

        <section className="contact-section" id="contact"><div><p className="eyebrow">Early access</p><h2>Keep a window<br /><em>open.</em></h2><p>We are shaping Tesseract carefully. Leave your email for product notes and early access updates.</p></div><form className="email-form" onSubmit={submitEmail}><label htmlFor="email">Your email address</label><div><input id="email" type="email" value={email} onChange={(event) => { setEmail(event.target.value); setFormMessage(""); }} placeholder="you@example.com" required /><button type="submit">Sign up <ArrowUpRight /></button></div><small aria-live="polite">{formMessage}</small></form></section>
      </main>
      <footer className="site-footer"><span>© 2026 Tesseract</span><span>Made for the space between questions.</span><div><button onClick={() => goTo("terms")}>Terms</button><button onClick={() => goTo("privacy")}>Privacy</button></div></footer>
    </div>
  );
}

export default App;
