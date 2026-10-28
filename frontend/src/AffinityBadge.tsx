import { useEffect, useState } from "react";

export interface AffinityBadgeProps {
  value: number;
}

/** Animated affinity badge: count-up from 0.0 to the card's multiplier.
/// DOM contract: always renders `.affinity-badge` with text
/// `N.N× your graph` (one decimal, finite-coerced) so every frame —
/// including the initial 0.0 — matches the acceptance regex. */
export default function AffinityBadge({ value }: AffinityBadgeProps) {
  const [display, setDisplay] = useState(0);

  // Self-contained rAF count-up (~900ms ease-out cubic); reduced-motion
  // jumps straight to the final value. Cleanup cancels the frame.
  useEffect(() => {
    const target = Number.isFinite(value) ? value : 1.0;
    if (
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      setDisplay(target);
      return;
    }
    let frame = 0;
    const duration = 900;
    const start =
      typeof performance !== "undefined" ? performance.now() : Date.now();
    const tick = (now: number) => {
      const elapsed = now - start;
      const t = Math.min(1, elapsed / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setDisplay(target * eased);
      if (t < 1) {
        frame = requestAnimationFrame(tick);
      } else {
        setDisplay(target);
      }
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value]);

  const formatted = (Number.isFinite(display) ? display : 1.0).toFixed(1);
  return (
    <span className="affinity-badge">{formatted}× your graph</span>
  );
}
