// Shared motion vocabulary. Keep interactions under 200ms and transitions 250–400ms,
// animating transform and opacity only.
import { useReducedMotion } from "motion/react";
import type { Transition, Variants } from "motion/react";

export const DURATION = { micro: 0.16, view: 0.32, launch: 1.2 } as const;
export const EASE_OUT = [0.22, 1, 0.36, 1] as const;
export const SPRING_PRESS: Transition = { type: "spring", stiffness: 520, damping: 30 };

const REDUCED_QUERY = "(prefers-reduced-motion: reduce)";

export const viewVariants: Variants = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0, transition: { duration: DURATION.view, ease: EASE_OUT } },
  exit: { opacity: 0, y: -6, transition: { duration: DURATION.micro, ease: EASE_OUT } },
};

export const chatVariants: Variants = {
  initial: { opacity: 0, y: 12 },
  animate: { opacity: 1, y: 0, transition: { duration: DURATION.view, ease: EASE_OUT } },
  exit: { opacity: 0, y: -8, transition: { duration: DURATION.micro, ease: EASE_OUT } },
};

/** Opacity-only versions used when the person asked the OS for less motion. */
export const calmVariants: Variants = {
  initial: { opacity: 0 },
  animate: { opacity: 1, transition: { duration: 0.12 } },
  exit: { opacity: 0, transition: { duration: 0.08 } },
};

/** True when the OS asks for reduced motion; read synchronously so the first frame is already calm. */
export function useReducedMotionPreference(): boolean {
  const fromMotion = useReducedMotion();
  if (fromMotion !== null) return fromMotion;
  return typeof window !== "undefined" && window.matchMedia(REDUCED_QUERY).matches;
}
