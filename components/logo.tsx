import { USE_MOCKS } from "@/lib/api";

export function Logo() {
  return (
    <span className="inline-flex items-center gap-2.5">
      <span className="flex size-8 items-center justify-center rounded-lg border border-ok/30 bg-ok/10 text-ok">
        <svg aria-hidden viewBox="0 0 24 24" fill="none" className="size-5">
          <path
            d="M2 12.5h5l2-4 3.2 9 2.8-12 2 7h5"
            stroke="currentColor"
            strokeWidth={1.8}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
      <span className="text-[15px] font-semibold tracking-tight">
        FlowDoctor <span className="font-normal text-muted-foreground">AI</span>
      </span>
    </span>
  );
}

export function MockBadge() {
  if (!USE_MOCKS) return null;
  return (
    <span className="rounded border border-warn/40 bg-warn/10 px-1.5 py-0.5 font-mono text-[10px] font-medium tracking-wider whitespace-nowrap text-warn">
      MOCK DATA
    </span>
  );
}
