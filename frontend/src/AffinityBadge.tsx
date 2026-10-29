import { AnimatedBadge } from "@/components/motion/animated-badge";
import { AnimatedNumber } from "@/components/motion/animated-number";

export interface AffinityBadgeProps {
  value: number;
}

/** Animated affinity badge: beUI animated-badge pill around a beUI
 *  animated-number count-up. DOM contract: always renders `.affinity-badge`
 *  with text `N.N× your graph` (one decimal, finite-coerced) on every frame,
 *  including the initial 0.0 the counter starts from. */
export default function AffinityBadge({ value }: AffinityBadgeProps) {
  const target = Number.isFinite(value) ? value : 1.0;
  return (
    <AnimatedBadge
      className="affinity-badge"
      status="info"
      showIcon={false}
      contentKey={target}
    >
      <AnimatedNumber
        value={target}
        duration={0.9}
        startOnView={false}
        format={(n) => `${(Number.isFinite(n) ? n : 1).toFixed(1)}× your graph`}
      />
    </AnimatedBadge>
  );
}
