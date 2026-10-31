import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage, openExternal } from "../../lib/sidecar";
import {
  Accounts,
  KeyAccount,
  KeyProvider,
  getAccounts,
  removeApiKey,
  saveApiKey,
  signOutChatGPT,
  startChatGPTSignIn,
} from "../../services/accounts";
import { TextField } from "../fields";

const POLL_MS = 2000;
const SIGN_IN_TIMEOUT_MS = 5 * 60 * 1000;
const USAGE_URL = "https://chatgpt.com/settings/usage";

function ExternalLink({ href, children }: Readonly<{ href: string; children: string }>) {
  return <a href={href} onClick={(event) => { event.preventDefault(); void openExternal(href); }}>{children}</a>;
}

interface ChatGPTCardProps {
  account: Accounts["chatgpt"];
  reload: () => Promise<Accounts | null>;
  onChange: () => void;
}

function ChatGPTCard({ account, reload, onChange }: Readonly<ChatGPTCardProps>) {
  const [waiting, setWaiting] = useState(false);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState("");
  const pollRef = useRef<number>();

  const stopWaiting = useCallback(() => {
    window.clearInterval(pollRef.current);
    setWaiting(false);
  }, []);
  useEffect(() => () => window.clearInterval(pollRef.current), []);

  const signIn = async (newAccount: boolean) => {
    setWorking(true);
    setMessage("");
    try {
      await openExternal(await startChatGPTSignIn(newAccount));
      setWaiting(true);
      const started = Date.now();
      // Asking again for plan use starts already connected, so only plan use being allowed ends the wait.
      const needsPlan = account.connected;
      window.clearInterval(pollRef.current);
      pollRef.current = window.setInterval(async () => {
        const next = await reload();
        if (next?.chatgpt.connected && (!needsPlan || next.chatgpt.plan_enabled)) {
          stopWaiting();
          onChange();
        } else if (Date.now() - started > SIGN_IN_TIMEOUT_MS) {
          stopWaiting();
          setMessage("Sign-in timed out. Start again when you are ready.");
        }
      }, POLL_MS);
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setWorking(false);
    }
  };

  const signOut = async () => {
    setWorking(true);
    setMessage("");
    try {
      const { revoked } = await signOutChatGPT();
      await reload();
      onChange();
      if (!revoked) {
        setMessage("Signed out here. ChatGPT couldn't confirm it, so you can also disconnect Tesseract in ChatGPT settings.");
      }
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setWorking(false);
    }
  };

  const usable = account.connected && account.plan_enabled;
  return (
    <div className="account-card">
      <h3>ChatGPT</h3>
      <div className="connection-status" role="status" aria-live="polite">
        {account.connected ? (
          <p><span className="status-dot status-dot-on" aria-hidden="true" /> Signed in as <strong>{account.email ?? "your ChatGPT account"}</strong></p>
        ) : (
          <p><span className="status-dot" aria-hidden="true" /> Not signed in</p>
        )}
      </div>
      {usable && <p className="field-hint">Using your ChatGPT plan. <ExternalLink href={USAGE_URL}>Manage usage</ExternalLink></p>}
      {account.connected && !account.plan_enabled && (
        <p className="field-hint">ChatGPT plan use isn't allowed for this account. Continue with ChatGPT to ask again.</p>
      )}
      {account.error && <p className="field-error">{account.error}</p>}
      <div className="button-row">
        {!usable && (
          <button type="button" className="primary-button" disabled={working || waiting} onClick={() => void signIn(false)}>
            Continue with ChatGPT
          </button>
        )}
        {account.connected && (
          <button type="button" className={usable ? "secondary-button" : "quiet-button"} disabled={working} onClick={() => void signOut()}>
            Sign out
          </button>
        )}
        {account.registered && !account.connected && (
          <button type="button" className="quiet-button" disabled={working || waiting} onClick={() => void signIn(true)}>
            Use a different account
          </button>
        )}
        {waiting && (
          <>
            <span className="field-hint">Finish signing in in your browser. This page updates on its own.</span>
            <button type="button" className="quiet-button" onClick={stopWaiting}>Cancel</button>
          </>
        )}
      </div>
      {message && <p className="field-hint" role="status">{message}</p>}
    </div>
  );
}

interface KeyCardProps {
  provider: KeyProvider;
  title: string;
  account: KeyAccount;
  keysUrl: string;
  placeholder: string;
  onUpdate: (account: KeyAccount) => void;
}

function KeyCard({ provider, title, account, keysUrl, placeholder, onUpdate }: Readonly<KeyCardProps>) {
  const [value, setValue] = useState("");
  const [replacing, setReplacing] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");

  const run = async (action: () => Promise<KeyAccount>) => {
    setWorking(true);
    setError("");
    try {
      onUpdate(await action());
      setValue("");
      setReplacing(false);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setWorking(false);
    }
  };

  const showField = !account.connected || replacing;
  return (
    <div className="account-card">
      <h3>{title}</h3>
      {account.connected && (
        <div className="connection-status">
          <p><span className="status-dot status-dot-on" aria-hidden="true" /> Key saved, ending in {account.key_hint?.replace(/^…/, "")}</p>
        </div>
      )}
      {showField && (
        <TextField
          label={`${title} API key`}
          secret
          value={value}
          placeholder={placeholder}
          error={error}
          onChange={setValue}
          hint={
            <>
              Create one at <ExternalLink href={keysUrl}>{keysUrl.replace("https://", "")}</ExternalLink>. It is stored with the system credential store, never in the settings file.
            </>
          }
        />
      )}
      <div className="button-row">
        {showField ? (
          <>
            <button type="button" className="primary-button" disabled={working || !value.trim()} onClick={() => void run(() => saveApiKey(provider, value.trim()))}>
              Save key
            </button>
            {replacing && (
              <button type="button" className="quiet-button" onClick={() => { setReplacing(false); setValue(""); setError(""); }}>Cancel</button>
            )}
          </>
        ) : (
          <>
            <button type="button" className="secondary-button" onClick={() => setReplacing(true)}>Replace key</button>
            <button type="button" className="danger-button" disabled={working} onClick={() => void run(() => removeApiKey(provider))}>Remove</button>
          </>
        )}
      </div>
      {!showField && error && <p className="field-error" role="alert">{error}</p>}
    </div>
  );
}

/** Model accounts. `onChange` fires when the set of usable models may have changed. */
export function AccountsSection({ onChange }: Readonly<{ onChange: () => void }>) {
  const [accounts, setAccounts] = useState<Accounts | null>(null);
  const [loadError, setLoadError] = useState("");

  const reload = useCallback(async () => {
    try {
      const next = await getAccounts();
      setAccounts(next);
      setLoadError("");
      return next;
    } catch (error) {
      setLoadError(errorMessage(error));
      return null;
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const setKey = (provider: KeyProvider) => (account: KeyAccount) => {
    setAccounts((current) => current && { ...current, [provider]: account });
    onChange();
  };

  if (!accounts) {
    return loadError
      ? <p className="field-error">Could not load your accounts: {loadError}</p>
      : <span className="skeleton skeleton-line" aria-label="Loading accounts" role="status" />;
  }

  return (
    <div className="account-cards">
      <ChatGPTCard account={accounts.chatgpt} reload={reload} onChange={onChange} />
      <KeyCard
        provider="anthropic"
        title="Claude (Anthropic)"
        account={accounts.anthropic}
        keysUrl="https://console.anthropic.com/settings/keys"
        placeholder="sk-ant-..."
        onUpdate={setKey("anthropic")}
      />
      <KeyCard
        provider="openrouter"
        title="OpenRouter"
        account={accounts.openrouter}
        keysUrl="https://openrouter.ai/keys"
        placeholder="sk-or-v1-..."
        onUpdate={setKey("openrouter")}
      />
    </div>
  );
}
