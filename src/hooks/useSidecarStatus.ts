import { useCallback, useEffect, useState } from "react";
import { checkSidecarHealth } from "../services/api";

export type SidecarState = "checking" | "online" | "offline";

const POLL_MS = 5000;

/** Tracks whether the local assistant service answers, re-checking periodically. */
export function useSidecarStatus() {
  const [state, setState] = useState<SidecarState>("checking");

  const check = useCallback(async () => {
    try {
      const health = await checkSidecarHealth();
      setState(health.status === "ok" ? "online" : "offline");
    } catch {
      setState("offline");
    }
  }, []);

  useEffect(() => {
    void check();
    const timer = window.setInterval(() => void check(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [check]);

  const retry = useCallback(() => {
    setState("checking");
    void check();
  }, [check]);

  return { state, retry };
}
