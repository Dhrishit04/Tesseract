import Lenis from "lenis";
import { ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useReducedMotionPreference } from "../lib/motion";

/** Distance from the bottom (px) after which we stop following new content. */
const FOLLOW_THRESHOLD = 120;
const savedPositions = new Map<string, number>();

interface SmoothScrollProps {
  children: ReactNode;
  label: string;
  /** Changes whenever content grows (e.g. the message list); triggers follow-to-bottom. */
  followKey: unknown;
  /** Identifies what is shown (e.g. the chat id) so each keeps its own scroll position. */
  restoreKey?: string;
  /** Whether there is anything to jump to (an empty view never shows the pill). */
  canJump?: boolean;
  className?: string;
}

/** Momentum-scrolled log that follows new content unless the reader scrolled up. */
export function SmoothScroll({ children, label, followKey, restoreKey, canJump = true, className }: Readonly<SmoothScrollProps>) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const lenisRef = useRef<Lenis | null>(null);
  const awayRef = useRef(false);
  const [away, setAway] = useState(false);
  const reduced = useReducedMotionPreference();

  useEffect(() => {
    const wrapper = wrapperRef.current;
    const content = contentRef.current;
    if (reduced || !wrapper || !content) return;
    const lenis = new Lenis({ wrapper, content, smoothWheel: true, lerp: 0.1, autoRaf: true });
    lenisRef.current = lenis;
    return () => {
      lenis.destroy();
      lenisRef.current = null;
    };
  }, [reduced]);

  const updateAway = useCallback(() => {
    const wrapper = wrapperRef.current;
    if (!wrapper) return;
    const next = wrapper.scrollHeight - wrapper.scrollTop - wrapper.clientHeight > FOLLOW_THRESHOLD;
    awayRef.current = next;
    setAway(next);
  }, []);

  const scrollToBottom = useCallback((smooth: boolean) => {
    const wrapper = wrapperRef.current;
    if (!wrapper) return;
    const target = wrapper.scrollHeight - wrapper.clientHeight;
    if (lenisRef.current) {
      lenisRef.current.resize();
      lenisRef.current.scrollTo(target, { immediate: !smooth });
    } else {
      wrapper.scrollTop = target;
    }
  }, []);

  // Each restoreKey keeps its own position; unseen content opens at the bottom.
  useLayoutEffect(() => {
    const wrapper = wrapperRef.current;
    if (!wrapper) return;
    const saved = restoreKey === undefined ? undefined : savedPositions.get(restoreKey);
    if (saved === undefined) {
      wrapper.scrollTop = wrapper.scrollHeight;
    } else {
      wrapper.scrollTop = saved;
      lenisRef.current?.scrollTo(saved, { immediate: true });
    }
    updateAway();
    return () => {
      if (restoreKey !== undefined) savedPositions.set(restoreKey, wrapper.scrollTop);
    };
  }, [restoreKey, updateAway]);

  useEffect(() => {
    if (!awayRef.current) scrollToBottom(false);
  }, [followKey, scrollToBottom]);

  return (
    <div className="smooth-scroll-frame">
      <div
        ref={wrapperRef}
        className={["smooth-scroll", className].filter(Boolean).join(" ")}
        role="log"
        aria-label={label}
        aria-live="polite"
        aria-relevant="additions"
        onScroll={updateAway}
      >
        <div ref={contentRef}>{children}</div>
      </div>
      {away && canJump && (
        <button type="button" className="jump-latest" onClick={() => scrollToBottom(!reduced)}>
          Jump to latest
        </button>
      )}
    </div>
  );
}
