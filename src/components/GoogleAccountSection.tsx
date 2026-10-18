import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage, openExternal } from "../lib/sidecar";
import { GoogleStatus, disconnectGoogle, getGoogleStatus, startGoogleSignIn } from "../services/google";
import { GoogleAuthSettings } from "../services/settings";
import { SettingsSection, TextField } from "./fields";

const POLL_MS = 2000;
const SIGN_IN_TIMEOUT_MS = 5 * 60 * 1000;

interface GoogleAccountSectionProps {
  google: GoogleAuthSettings;
  onChange: (google: GoogleAuthSettings) => void;
}

export function GoogleAccountSection({ google, onChange }: Readonly<GoogleAccountSectionProps>) {
  const [status, setStatus] = useState<GoogleStatus | null>(null);
  const [statusError, setStatusError] = useState("");
  const [waiting, setWaiting] = useState(false);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState("");
  const pollRef = useRef<number>();

  const loadStatus = useCallback(async () => {
    try {
      const next = await getGoogleStatus();
      setStatus(next);
      setStatusError("");
      return next;
    } catch (error) {
      setStatusError(errorMessage(error));
      return null;
    }
  }, []);

  const stopWaiting = useCallback(() => {
    window.clearInterval(pollRef.current);
    setWaiting(false);
  }, []);

  useEffect(() => {
    void loadStatus();
    return () => window.clearInterval(pollRef.current);
  }, [loadStatus]);

  const connect = async () => {
    setWorking(true);
    setMessage("");
    try {
      const url = await startGoogleSignIn(google.client_id.trim(), google.client_secret.trim());
      await openExternal(url);
      setWaiting(true);
      const started = Date.now();
      pollRef.current = window.setInterval(async () => {
        const next = await loadStatus();
        if (next?.connected) {
          stopWaiting();
          setMessage(`Connected as ${next.email ?? "your Google account"}.`);
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

  const disconnect = async () => {
    setWorking(true);
    setMessage("");
    try {
      await disconnectGoogle();
      await loadStatus();
      setMessage("Google account disconnected and access revoked.");
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setWorking(false);
    }
  };

  const notInstalled = status?.error?.includes("not installed");
  const canConnect = Boolean(google.client_id.trim() && google.client_secret.trim()) && !working && !waiting;

  return (
    <SettingsSection
      id="google"
      title="Google account"
      description="Connect Gmail and Google Calendar. Tesseract uses your own OAuth client from Google Cloud Console, with http://localhost:8765/oauth/callback as the redirect URI."
    >
      <div className="connection-status" role="status" aria-live="polite">
        {!status && !statusError && <span className="skeleton skeleton-line skeleton-short" aria-label="Checking connection" />}
        {statusError && <p className="field-error">Could not check the connection: {statusError}</p>}
        {status?.connected && (
          <p><span className="status-dot status-dot-on" aria-hidden="true" /> Connected as <strong>{status.email ?? "your Google account"}</strong></p>
        )}
        {status && !status.connected && !notInstalled && <p><span className="status-dot" aria-hidden="true" /> Not connected</p>}
        {notInstalled && <p className="field-error">{status?.error}</p>}
      </div>

      {status?.connected ? (
        <div className="button-row">
          <button type="button" className="danger-button" disabled={working} onClick={() => void disconnect()}>
            Disconnect and revoke access
          </button>
        </div>
      ) : (
        <>
          <TextField
            label="OAuth client ID"
            value={google.client_id}
            placeholder="1234-abc.apps.googleusercontent.com"
            onChange={(client_id) => onChange({ ...google, client_id })}
          />
          <TextField
            label="OAuth client secret"
            secret
            value={google.client_secret}
            onChange={(client_secret) => onChange({ ...google, client_secret })}
          />
          <div className="button-row">
            <button type="button" className="primary-button" disabled={!canConnect || notInstalled} onClick={() => void connect()}>
              Connect Google account
            </button>
            {waiting && (
              <>
                <span className="field-hint">Finish signing in in your browser. This page updates on its own.</span>
                <button type="button" className="quiet-button" onClick={stopWaiting}>Cancel</button>
              </>
            )}
          </div>
        </>
      )}
      {message && <p className="field-hint" role="status">{message}</p>}
    </SettingsSection>
  );
}
