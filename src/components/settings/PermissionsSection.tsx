import { useCallback, useEffect, useState } from "react";
import { errorMessage } from "../../lib/sidecar";
import { TrustedTool, listTrusted, revokeTrusted } from "../../services/permissions";
import { SettingsSection } from "../fields";

/** The "Always allow" list. Revoking applies immediately. */
export function PermissionsSection() {
  const [trusted, setTrusted] = useState<TrustedTool[] | null>(null);
  const [loadError, setLoadError] = useState("");
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [revoking, setRevoking] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoadError("");
    try {
      setTrusted(await listTrusted());
    } catch (error) {
      setLoadError(errorMessage(error));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const revoke = async (tool: string) => {
    setRevoking(tool);
    setRowErrors(({ [tool]: _cleared, ...rest }) => rest);
    try {
      await revokeTrusted(tool);
      setTrusted((current) => current?.filter((row) => row.tool !== tool) ?? null);
    } catch (error) {
      setRowErrors((all) => ({ ...all, [tool]: `Not revoked. ${errorMessage(error)}` }));
    } finally {
      setRevoking(null);
    }
  };

  let body;
  if (loadError) {
    body = (
      <div className="inline-error" role="alert">
        <p>The list could not be loaded. {loadError}</p>
        <button type="button" className="text-button" onClick={() => void load()}>Try again</button>
      </div>
    );
  } else if (!trusted) {
    body = <span className="skeleton skeleton-line skeleton-short" aria-label="Loading permissions" />;
  } else if (trusted.length === 0) {
    body = <p className="field-hint">Nothing is always allowed. When you choose Always allow this on a request, it appears here.</p>;
  } else {
    body = (
      <ul className="trusted-list" aria-label="Always allowed">
        {trusted.map((row) => (
          <li key={row.tool} className="trusted-row">
            <span title={row.tool}>{row.title}</span>
            <button
              type="button"
              className="secondary-button"
              aria-label={`Revoke ${row.title}`}
              disabled={revoking === row.tool}
              onClick={() => void revoke(row.tool)}
            >
              Revoke
            </button>
            {rowErrors[row.tool] && <p className="field-error" role="alert">{rowErrors[row.tool]}</p>}
          </li>
        ))}
      </ul>
    );
  }

  return (
    <SettingsSection
      id="permissions"
      title="Permissions"
      description="Tesseract reads freely but asks before it changes anything. Actions you chose Always allow for run without asking."
    >
      {body}
    </SettingsSection>
  );
}
