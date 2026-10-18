import { useEffect, useState } from "react";
import packageInfo from "../../../package.json";
import type { SidecarState } from "../../hooks/useSidecarStatus";
import { CAPABILITY_GROUPS, CapabilityGroup } from "../../lib/capabilities";
import { errorMessage } from "../../lib/sidecar";
import { SkillInfo, listSkills } from "../../services/api";
import { getGoogleStatus } from "../../services/google";
import { getSettings } from "../../services/settings";
import { SettingsSection } from "../fields";

interface LiveState {
  skills: SkillInfo[];
  googleConnected: boolean;
  automationEnabled: boolean;
}

type Readiness = { ready: boolean; text: string; fixHref?: string };

function readiness(group: CapabilityGroup, actions: number, live: LiveState): Readiness {
  if (actions === 0) return { ready: false, text: "Not installed on this computer" };
  if (group.requires === "google" && !live.googleConnected) return { ready: false, text: "Connect a Google account", fixHref: "#/settings/google" };
  if (group.requires === "automation" && !live.automationEnabled) return { ready: false, text: "Allow automation", fixHref: "#/settings/automation" };
  return { ready: true, text: `Ready, ${actions} ${actions === 1 ? "action" : "actions"}` };
}

function CapabilityList({ sidecar }: Readonly<{ sidecar: SidecarState }>) {
  const [live, setLive] = useState<LiveState | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (sidecar !== "online") return;
    let cancelled = false;
    Promise.all([listSkills(), getGoogleStatus().catch(() => null), getSettings().catch(() => null)])
      .then(([skills, google, settings]) => {
        if (cancelled) return;
        setLive({ skills, googleConnected: Boolean(google?.connected), automationEnabled: Boolean(settings?.automation.automation_enabled) });
        setError("");
      })
      .catch((failure) => !cancelled && setError(errorMessage(failure)));
    return () => {
      cancelled = true;
    };
  }, [sidecar]);

  return (
    <>
      {sidecar === "offline" && <p className="inline-error" role="alert">The assistant service is offline, so capabilities cannot be checked.</p>}
      {error && <p className="inline-error" role="alert">Capabilities could not be loaded. {error}</p>}
      <dl className="capability-list" aria-busy={!live}>
        {CAPABILITY_GROUPS.map((group) => {
          const actions = live?.skills.filter((skill) => skill.name.startsWith(`${group.prefix}.`)).length ?? 0;
          const state = live ? readiness(group, actions, live) : null;
          return (
            <div className="capability" key={group.prefix}>
              <dt>{group.title}</dt>
              <dd className="capability-summary">{group.summary}</dd>
              <dd className="capability-state">
                {!state && <span className="skeleton skeleton-line skeleton-short" aria-label="Checking" />}
                {state?.ready && <span className="state-ready"><span className="status-dot status-dot-on" aria-hidden="true" />{state.text}</span>}
                {state && !state.ready && !state.fixHref && <span className="state-muted">{state.text}</span>}
                {state?.fixHref && <a className="text-button" href={state.fixHref}>{state.text}</a>}
              </dd>
            </div>
          );
        })}
      </dl>
    </>
  );
}

interface AboutSectionProps {
  sidecar: SidecarState;
  configPath: string;
  onReset: () => Promise<void>;
}

export function AboutSection({ sidecar, configPath, onReset }: Readonly<AboutSectionProps>) {
  const [confirmReset, setConfirmReset] = useState(false);
  return (
    <>
      <SettingsSection id="about" title="About Tesseract" description={`Version ${packageInfo.version}. A desktop assistant that works on this computer with the model you choose.`}>
        <CapabilityList sidecar={sidecar} />
        <ul className="facts">
          <li>Press <kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>P</kbd> anywhere to show or hide Tesseract. Closing the window keeps it running in the tray.</li>
          <li>Automation is off until you allow it, and file access is limited to the assistant's working folder.</li>
        </ul>
        <p className="button-row">
          <a className="secondary-button" href="#/settings/about/terms">Terms of Service</a>
          <a className="secondary-button" href="#/settings/about/privacy">Privacy Policy</a>
        </p>
      </SettingsSection>

      <SettingsSection id="data" title="Data on this computer" description="Settings, saved chats and Google tokens are kept in your user folder.">
        <p className="mono-path">{configPath}</p>
        {confirmReset ? (
          <div className="button-row" role="group" aria-label="Confirm reset">
            <span className="field-hint">This clears your API key and Google client details. Saved chats are kept.</span>
            <button type="button" className="danger-button" onClick={() => { setConfirmReset(false); void onReset(); }}>Reset settings</button>
            <button type="button" className="quiet-button" onClick={() => setConfirmReset(false)}>Keep my settings</button>
          </div>
        ) : (
          <div className="button-row">
            <button type="button" className="secondary-button" onClick={() => setConfirmReset(true)}>Reset to defaults</button>
          </div>
        )}
      </SettingsSection>
    </>
  );
}
