import { ReactNode } from "react";

interface LegalSection {
  title: string;
  body: ReactNode;
}

interface LegalDocument {
  title: string;
  intro: string;
  sections: LegalSection[];
  missing: string[];
}

const PRIVACY: LegalDocument = {
  title: "Privacy Policy",
  intro:
    "This page describes what the Tesseract desktop app does with your data, based on how the current version works. It has not been reviewed by a lawyer.",
  sections: [
    {
      title: "What stays on your computer",
      body: (
        <>
          <p>
            Your settings are saved in <code>~/.tesseract/settings.json</code>. This file holds your OpenRouter
            API key, your Google OAuth client ID and secret, your Picovoice access key and your preferences.
            It is stored as plain text, protected only by your operating system account.
          </p>
          <p>
            After you connect a Google account, its access and refresh tokens are saved in{" "}
            <code>~/.tesseract/tokens/</code>, encrypted with AES-256-GCM. The encryption key is kept in the
            same folder.
          </p>
          <p>Conversations are kept in memory while the app is open. They are not written to disk.</p>
        </>
      ),
    },
    {
      title: "What is sent to other services",
      body: (
        <>
          <p>
            <strong>OpenRouter.</strong> Each message you send, the earlier messages in the same conversation,
            and the results of any actions Tesseract took go to OpenRouter, which passes them to the model
            provider you selected. Action results can include file contents, email text, calendar events,
            web page text and screenshots.
          </p>
          <p>
            <strong>Google.</strong> When your Google account is connected, Tesseract calls the Gmail and Google
            Calendar APIs directly from your computer, only when a request needs them. It asks for permission
            to read, send and modify email, and to read and edit calendar events. Disconnecting in Settings
            revokes that permission with Google and deletes the stored tokens.
          </p>
          <p>
            <strong>Voice.</strong> Speech recognition (whisper.cpp) and the Piper voice run on your computer.
            The wake word engine (Porcupine) checks your access key with Picovoice. If you choose the OpenRouter
            voice, the text to be spoken is sent to OpenRouter.
          </p>
          <p>Each of these services handles data under its own terms and privacy policy.</p>
        </>
      ),
    },
    {
      title: "What Tesseract does not do",
      body: (
        <p>
          The app has no user accounts, analytics or usage tracking, and it does not send data to any server
          run by the Tesseract project.
        </p>
      ),
    },
  ],
  missing: [
    "Name and contact details of the publisher responsible for the app",
    "Effective date and how changes will be announced",
    "Your rights under the laws that apply to you, such as GDPR, and how to exercise them",
    "Whether the app is intended for people under a minimum age",
  ],
};

const TERMS: LegalDocument = {
  title: "Terms of Service",
  intro:
    "These terms describe how Tesseract works and what you are responsible for when you use it. They are a working draft and are not yet a binding agreement.",
  sections: [
    {
      title: "What Tesseract is",
      body: (
        <p>
          Tesseract is a desktop assistant that sends your requests to an AI model you choose through OpenRouter
          and can act on your computer: reading and writing files as far as you allow in Settings, opening apps, using a
          browser and desktop apps, editing Office documents, and using your Gmail and Google Calendar once
          you connect them.
        </p>
      ),
    },
    {
      title: "Your accounts and keys",
      body: (
        <p>
          You supply your own OpenRouter API key, Google OAuth client and Picovoice key. Charges from those
          services are billed to you by them, and your use of each is governed by its own terms.
        </p>
      ),
    },
    {
      title: "Actions taken on your behalf",
      body: (
        <p>
          AI models can misunderstand requests and make mistakes. When automation is allowed, the model can
          click, type, send email and change calendar events without asking again for each step. Review what
          Tesseract did in the activity log, check its answers before relying on them, and leave automation
          off if you do not want the model to operate your computer.
        </p>
      ),
    },
  ],
  missing: [
    "Who publishes Tesseract and how to contact them",
    "Software licence and permitted use",
    "Warranty, liability and indemnity terms",
    "Governing law, dispute resolution and effective date",
  ],
};

export function LegalPage({ kind }: Readonly<{ kind: "terms" | "privacy" }>) {
  const doc = kind === "privacy" ? PRIVACY : TERMS;
  return (
    <article className="page legal">
      <p className="draft-flag">Draft for review</p>
      <h1 className="page-title">{doc.title}</h1>
      <p className="page-lede">{doc.intro}</p>
      {doc.sections.map((section) => (
        <section key={section.title} className="legal-section">
          <h2>{section.title}</h2>
          {section.body}
        </section>
      ))}
      <section className="legal-section legal-missing">
        <h2>Still to be completed before release</h2>
        <ul>
          {doc.missing.map((item) => <li key={item}>{item}</li>)}
        </ul>
      </section>
      <p className="legal-meta">Describes the app as of version 0.1.0.</p>
    </article>
  );
}
