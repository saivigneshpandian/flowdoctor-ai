import { ConnectForm } from "@/components/connect-form";
import { EcgTrace } from "@/components/ecg-trace";
import { Logo, MockBadge } from "@/components/logo";

export default function ConnectPage() {
  return (
    <main className="ecg-paper relative flex min-h-dvh flex-1 flex-col overflow-hidden">
      <div aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 h-24 opacity-70">
        <EcgTrace tone="ok" />
      </div>

      <header className="relative flex items-center justify-between gap-3 px-6 py-5 lg:px-10">
        <Logo />
        <MockBadge />
      </header>

      <div className="relative mx-auto grid w-full max-w-6xl flex-1 items-center gap-10 px-6 pb-12 lg:grid-cols-[1fr_27rem] lg:gap-16 lg:px-10">
        <div className="max-w-xl">
          <p className="chart-label">On call for your n8n workflows</p>
          <h1 className="mt-3 text-4xl leading-[1.08] font-semibold tracking-tight text-balance sm:text-5xl lg:text-6xl">
            Connect your instance. We&apos;ll take its vitals.
          </h1>
          <p className="mt-5 max-w-md text-base leading-relaxed text-muted-foreground">
            See workflow health, ask why something failed, and review each proposed fix. The agent
            never changes anything without your approval.
          </p>
        </div>

        <section aria-label="Connection details" className="rounded-2xl border bg-card/95 p-6 shadow-[0_24px_70px_-30px_rgb(0_0_0/80%)] backdrop-blur-sm sm:p-7">
          <ConnectForm />
        </section>
      </div>
    </main>
  );
}
