import { useIsPresent } from "motion/react";
import { ReactNode, useEffect, useRef } from "react";

/**
 * Wraps an animated view. While it is animating out it becomes inert, so a fast click
 * or keystroke cannot land in the screen that is leaving (e.g. the previous chat's composer).
 */
export function ViewFrame({ children }: Readonly<{ children: ReactNode }>) {
  const isPresent = useIsPresent();
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (ref.current) ref.current.inert = !isPresent;
  }, [isPresent]);

  return (
    <div ref={ref} className="view-frame">
      {children}
    </div>
  );
}
