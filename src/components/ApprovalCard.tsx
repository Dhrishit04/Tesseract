import { motion } from "motion/react";
import type { ApprovalDecision } from "../services/api";
import { ApprovalState, approvalNote } from "../lib/chatEvents";
import { approvalVariants, calmVariants, useReducedMotionPreference } from "../lib/motion";

const LONG_VALUE = 160;

function showValue(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

interface ApprovalCardProps {
  approval: ApprovalState;
  onAnswer: (approvalId: string, decision: ApprovalDecision) => void;
}

/** A change Tesseract wants to make, with the owner's three answers. */
export function ApprovalCard({ approval, onAnswer }: Readonly<ApprovalCardProps>) {
  const reduced = useReducedMotionPreference();
  const open = approval.status === "pending" || approval.status === "answering";
  const answering = approval.status === "answering";
  const details = Object.entries(approval.details);

  return (
    <motion.section
      className={`approval-card approval-${approval.status}`}
      aria-label={`Approval needed: ${approval.title}`}
      variants={reduced ? calmVariants : approvalVariants}
      initial="initial"
      animate="animate"
    >
      <header className="approval-head">
        <h3>{approval.title}</h3>
        {approval.irreversible && <span className="approval-badge">Can't be undone</span>}
      </header>
      <p className="approval-summary">{approval.summary}</p>
      {details.length > 0 && (
        <dl className="approval-details">
          {details.map(([key, value]) => {
            const text = showValue(value);
            const long = text.length > LONG_VALUE || text.includes("\n");
            return (
              <div key={key}>
                <dt>{key}</dt>
                {/* A long value scrolls; focusable so it can be scrolled from the keyboard. */}
                <dd className={long ? "approval-value approval-value-long" : "approval-value"} tabIndex={long ? 0 : undefined}>
                  {text}
                </dd>
              </div>
            );
          })}
        </dl>
      )}
      {open ? (
        <>
          <div className="approval-actions">
            <button type="button" className="primary-button" disabled={answering} onClick={() => onAnswer(approval.id, "allow")}>
              Allow
            </button>
            <button type="button" className="secondary-button" disabled={answering} onClick={() => onAnswer(approval.id, "always")}>
              Always allow this
            </button>
            <button type="button" className="quiet-button" disabled={answering} onClick={() => onAnswer(approval.id, "deny")}>
              Don't allow
            </button>
          </div>
          {answering && <p className="approval-note" role="status">Checking your answer…</p>}
          {approval.error && <p className="field-error" role="alert">{approval.error}</p>}
        </>
      ) : (
        <p className="approval-note" role="status">{approval.error ?? approvalNote(approval)}</p>
      )}
    </motion.section>
  );
}
