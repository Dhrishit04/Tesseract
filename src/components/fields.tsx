import { ReactNode, useId, useState } from "react";

interface FieldShellProps {
  id: string;
  label: string;
  hint?: ReactNode;
  error?: string;
  children: ReactNode;
}

function FieldShell({ id, label, hint, error, children }: Readonly<FieldShellProps>) {
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>{label}</label>
      {children}
      {hint && <p className="field-hint" id={`${id}-hint`}>{hint}</p>}
      {error && <p className="field-error" id={`${id}-error`} role="alert">{error}</p>}
    </div>
  );
}

function describedBy(id: string, hint?: ReactNode, error?: string) {
  return [hint ? `${id}-hint` : "", error ? `${id}-error` : ""].filter(Boolean).join(" ") || undefined;
}

interface TextFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: ReactNode;
  error?: string;
  placeholder?: string;
  secret?: boolean;
  type?: "text" | "number";
  inputMode?: "numeric" | "text";
  autoComplete?: string;
}

export function TextField({ label, value, onChange, hint, error, placeholder, secret, type = "text", inputMode, autoComplete = "off" }: Readonly<TextFieldProps>) {
  const id = useId();
  const [revealed, setRevealed] = useState(false);
  const input = (
    <input
      id={id}
      type={secret && !revealed ? "password" : type}
      value={value}
      placeholder={placeholder}
      inputMode={inputMode}
      autoComplete={autoComplete}
      spellCheck={false}
      aria-invalid={error ? true : undefined}
      aria-describedby={describedBy(id, hint, error)}
      onChange={(event) => onChange(event.target.value)}
    />
  );
  return (
    <FieldShell id={id} label={label} hint={hint} error={error}>
      {secret ? (
        <div className="secret-input">
          {input}
          <button type="button" className="secondary-button" aria-pressed={revealed} onClick={() => setRevealed((shown) => !shown)}>
            {revealed ? "Hide" : "Show"}
          </button>
        </div>
      ) : (
        input
      )}
    </FieldShell>
  );
}

interface TextAreaFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: ReactNode;
  placeholder?: string;
}

export function TextAreaField({ label, value, onChange, hint, placeholder }: Readonly<TextAreaFieldProps>) {
  const id = useId();
  return (
    <FieldShell id={id} label={label} hint={hint}>
      <textarea
        id={id}
        value={value}
        placeholder={placeholder}
        aria-describedby={describedBy(id, hint)}
        onChange={(event) => onChange(event.target.value)}
      />
    </FieldShell>
  );
}

interface SelectFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  hint?: ReactNode;
}

export function SelectField({ label, value, onChange, options, hint }: Readonly<SelectFieldProps>) {
  const id = useId();
  const list = options.some((option) => option.value === value) ? options : [{ value, label: value }, ...options];
  return (
    <FieldShell id={id} label={label} hint={hint}>
      <select id={id} value={value} aria-describedby={describedBy(id, hint)} onChange={(event) => onChange(event.target.value)}>
        {list.map((option) => (
          <option key={option.value} value={option.value}>{option.label}</option>
        ))}
      </select>
    </FieldShell>
  );
}

interface ToggleProps {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  hint?: ReactNode;
  disabled?: boolean;
}

export function Toggle({ label, checked, onChange, hint, disabled }: Readonly<ToggleProps>) {
  const id = useId();
  return (
    <div className="toggle">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        aria-describedby={hint ? `${id}-hint` : undefined}
        onChange={(event) => onChange(event.target.checked)}
      />
      <div>
        <label htmlFor={id}>{label}</label>
        {hint && <p className="field-hint" id={`${id}-hint`}>{hint}</p>}
      </div>
    </div>
  );
}

interface SettingsSectionProps {
  id: string;
  title: string;
  description: ReactNode;
  children: ReactNode;
}

export function SettingsSection({ id, title, description, children }: Readonly<SettingsSectionProps>) {
  return (
    <section className="settings-section" aria-labelledby={`${id}-title`} id={id}>
      <div className="settings-section-intro">
        <h2 id={`${id}-title`}>{title}</h2>
        <p>{description}</p>
      </div>
      <div className="settings-section-body">{children}</div>
    </section>
  );
}
