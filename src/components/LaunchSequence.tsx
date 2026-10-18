import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";
import { DURATION, EASE_OUT, useReducedMotionPreference } from "../lib/motion";

// Shown once per page load: the app "wakes up" instead of just appearing.
let alreadyShown = false;

const DRAW = { duration: DURATION.launch * 0.55, ease: EASE_OUT };
const HOLD_MS = DURATION.launch * 1000 * 0.75;

export function LaunchSequence({ onDone }: Readonly<{ onDone?: () => void }>) {
  const reduced = useReducedMotionPreference();
  const [visible, setVisible] = useState(() => !alreadyShown && !reduced);

  useEffect(() => {
    alreadyShown = true;
    if (!visible) {
      onDone?.();
      return;
    }
    const timer = window.setTimeout(() => setVisible(false), HOLD_MS);
    return () => window.clearTimeout(timer);
    // Runs once: the sequence plays a single time per load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <AnimatePresence onExitComplete={onDone}>
      {visible && (
        <motion.div
          className="launch-sequence"
          aria-hidden="true"
          initial={{ opacity: 1 }}
          exit={{ opacity: 0, transition: { duration: DURATION.launch * 0.25, ease: EASE_OUT } }}
        >
          <motion.svg
            viewBox="0 0 48 48"
            width="112"
            height="112"
            fill="none"
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
            initial={{ scale: 0.92, opacity: 0 }}
            animate={{ scale: 1, opacity: 1, transition: { duration: DURATION.view, ease: EASE_OUT } }}
            exit={{ scale: 1.08 }}
          >
            <motion.rect x="4" y="4" width="40" height="40" strokeWidth="1.6" initial={{ pathLength: 0 }} animate={{ pathLength: 1, transition: DRAW }} />
            <motion.rect x="16" y="16" width="16" height="16" strokeWidth="1.6" initial={{ pathLength: 0 }} animate={{ pathLength: 1, transition: { ...DRAW, delay: 0.12 } }} />
            <motion.path d="M4 4 16 16M44 4 32 16M44 44 32 32M4 44 16 32" strokeWidth="1.1" initial={{ pathLength: 0 }} animate={{ pathLength: 1, transition: { ...DRAW, delay: 0.24 } }} />
          </motion.svg>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
