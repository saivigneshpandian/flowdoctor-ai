import { cn } from "@/lib/utils";

export type TraceTone = "ok" | "fail" | "warn" | "idle";

const BEAT = "h34 l6 -5 l6 5 h10 l4 4 l6 -34 l7 44 l5 -14 h12 q8 -9 16 0 h44";
const ARRHYTHMIA = "h14 l5 -22 l6 34 l4 -12 h22 l3 3 l6 -38 l5 54 l5 -20 h10 l6 -14 l5 20 h49";

const PATHS: Record<TraceTone, string> = {
  ok: `M0 40 ${BEAT.repeat(1)} ${BEAT} ${BEAT} ${BEAT}`,
  warn: `M0 40 ${BEAT} h40 ${BEAT} h40 ${BEAT}`,
  fail: `M0 40 ${ARRHYTHMIA} ${ARRHYTHMIA} ${ARRHYTHMIA} ${ARRHYTHMIA}`,
  idle: "M0 40 h600",
};

const COLORS: Record<TraceTone, string> = {
  ok: "text-ok",
  warn: "text-warn",
  fail: "text-fail",
  idle: "text-muted-foreground",
};

// Decorative monitor sweep. Its tone always comes from real status, and "idle" means no data yet.
export function EcgTrace({ tone, className }: { tone: TraceTone; className?: string }) {
  const d = PATHS[tone];
  return (
    <svg
      aria-hidden
      viewBox="0 0 600 80"
      preserveAspectRatio="none"
      fill="none"
      className={cn("h-full w-full", COLORS[tone], className)}
    >
      <path d={d} stroke="currentColor" strokeOpacity={0.14} strokeWidth={1.25} vectorEffect="non-scaling-stroke" />
      {tone !== "idle" && (
        <path
          d={d}
          pathLength={1}
          className="ecg-sweep"
          stroke="currentColor"
          strokeWidth={1.5}
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
        />
      )}
    </svg>
  );
}
