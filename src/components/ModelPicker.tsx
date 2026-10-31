import { ReactNode, useEffect, useState } from "react";
import { groupModels, pickAvailable, providerOf } from "../lib/models";
import { openExternal } from "../lib/sidecar";
import { ModelList, listModels } from "../services/api";

interface ModelPickerProps {
  currentModel: string;
  onModelChange: (model: string) => void;
  /** Visible label (Settings). Without it the label is hidden from view but still read aloud. */
  label?: string;
  hint?: ReactNode;
  /** Swap an unusable model for a listed one. Off for saved chats: viewing a chat must not rewrite its model. */
  autoPick?: boolean;
  /** Shown instead of the "Connect a model" button when nothing is connected, where the accounts are already on screen. */
  connectHint?: string;
}

const USAGE_URL = "https://chatgpt.com/settings/usage";

export function ModelPicker({ currentModel, onModelChange, label, hint, autoPick = true, connectHint }: Readonly<ModelPickerProps>) {
  const [list, setList] = useState<ModelList | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setList(null);
    setFailed(false);
    listModels()
      .then((result) => !cancelled && setList(result))
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  // Swap a model no connected account serves (the built-in default, or a signed-out provider's) for one that works.
  useEffect(() => {
    if (!list || !autoPick) return;
    const available = pickAvailable(currentModel, list);
    if (available !== currentModel) onModelChange(available);
  }, [list, autoPick, currentModel, onModelChange]);

  if (failed) {
    return (
      <span className="model-picker">
        <span className="field-hint" role="alert">Couldn't load models.</span>
        <button type="button" className="text-button" onClick={() => setAttempt((count) => count + 1)}>Try again</button>
      </span>
    );
  }

  if (!list) {
    return <span className="skeleton skeleton-select" aria-label="Loading models" role="status" />;
  }

  const errors = Object.entries(list.errors).map(([provider, message]) => (
    <p key={provider} className="field-hint" role="alert">{message}</p>
  ));

  if (!list.models.length) {
    if (errors.length) return <div className="model-picker">{errors}</div>;
    if (connectHint) return <p className="field-hint">{connectHint}</p>;
    return (
      <button type="button" className="secondary-button" onClick={() => { window.location.hash = "#/settings/models"; }}>
        Connect a model
      </button>
    );
  }

  const listed = list.models.some((model) => model.id === currentModel);

  return (
    <div className={label ? "model-picker field" : "model-picker"}>
      <label htmlFor="model-picker" className={label ? "field-label" : "visually-hidden"}>{label ?? "Model"}</label>
      <select id="model-picker" name="model" value={currentModel} onChange={(event) => onModelChange(event.target.value)}>
        {!listed && <option value={currentModel}>{currentModel}</option>}
        {groupModels(list.models).map((group) => (
          <optgroup key={group.provider} label={group.label}>
            {group.models.map((model) => (
              <option key={model.id} value={model.id}>{model.name ?? model.id}</option>
            ))}
          </optgroup>
        ))}
      </select>
      {errors}
      {hint && <p className="field-hint">{hint}</p>}
      {providerOf(currentModel) === "chatgpt" && (
        <p className="field-hint">
          Using ChatGPT plan ·{" "}
          <a href={USAGE_URL} onClick={(event) => { event.preventDefault(); void openExternal(USAGE_URL); }}>Manage usage</a>
        </p>
      )}
    </div>
  );
}
