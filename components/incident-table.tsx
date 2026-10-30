import { StatusPill, type CardTone } from "@/components/cards/card-shell";
import { formatTimestamp } from "@/lib/format";
import type { Incident } from "@/lib/types";

export type IncidentFilter = "all" | "fixed" | "rejected" | "open" | "manual";

// An approved or manual fix only counts as finished once the backend has recorded a resolution.
export function incidentBucket(incident: Incident): Exclude<IncidentFilter, "all"> {
  if (incident.fix_status === "rejected") return "rejected";
  if (incident.fix_status === "open" || !incident.resolved_at) return "open";
  return incident.fix_status === "manual" ? "manual" : "fixed";
}

function incidentStatus(incident: Incident): { tone: CardTone; label: string } {
  const resolved = Boolean(incident.resolved_at);
  switch (incident.fix_status) {
    case "approved":
      return resolved
        ? { tone: "ok", label: "Fixed · approved" }
        : { tone: "warn", label: "Approved · in progress" };
    case "manual":
      return resolved
        ? { tone: "ok", label: "Fixed · manual" }
        : { tone: "warn", label: "Waiting on you" };
    case "rejected":
      return { tone: "neutral", label: "Rejected" };
    case "open":
      return { tone: "fail", label: "Open" };
  }
}

const CATEGORY = { auto_fixable: "Auto-fixable", needs_user: "Needs your action" };

export function IncidentTable({ incidents }: { incidents: Incident[] }) {
  return (
    <>
      <div className="hidden overflow-hidden rounded-xl border bg-card md:block">
        <table className="w-full text-left text-sm">
          <thead className="border-b">
            <tr>
              {["Status", "Workflow", "Diagnosis", "Opened", "Repaired"].map((heading) => (
                <th key={heading} scope="col" className="chart-label px-4 py-3 font-normal">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y">
            {incidents.map((incident) => {
              const status = incidentStatus(incident);
              return (
                <tr key={incident.id} className="align-top">
                  <td className="px-4 py-3.5">
                    <StatusPill tone={status.tone}>{status.label}</StatusPill>
                  </td>
                  <td className="px-4 py-3.5">
                    <p className="font-medium">{incident.workflow_name}</p>
                    <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                      {incident.failed_node}
                    </p>
                  </td>
                  <td className="max-w-md px-4 py-3.5">
                    <p className="leading-relaxed">{incident.root_cause}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {CATEGORY[incident.category]}
                    </p>
                  </td>
                  <td className="px-4 py-3.5 font-mono text-xs whitespace-nowrap text-muted-foreground">
                    {formatTimestamp(incident.created_at)}
                  </td>
                  <td className="px-4 py-3.5 font-mono text-xs whitespace-nowrap text-muted-foreground">
                    {incident.resolved_at && incident.fix_status !== "rejected"
                      ? formatTimestamp(incident.resolved_at)
                      : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <ul className="grid gap-3 md:hidden">
        {incidents.map((incident) => {
          const status = incidentStatus(incident);
          return (
            <li key={incident.id} className="rounded-xl border bg-card p-4">
              <StatusPill tone={status.tone}>{status.label}</StatusPill>
              <p className="mt-2.5 font-medium">{incident.workflow_name}</p>
              <p className="mt-0.5 font-mono text-xs text-muted-foreground">{incident.failed_node}</p>
              <p className="mt-2 text-sm leading-relaxed">{incident.root_cause}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">{CATEGORY[incident.category]}</p>
              <p className="mt-2.5 font-mono text-xs text-muted-foreground">
                Opened {formatTimestamp(incident.created_at)}
                {incident.resolved_at && incident.fix_status !== "rejected" && (
                  <>
                    <br />
                    Repaired {formatTimestamp(incident.resolved_at)}
                  </>
                )}
              </p>
            </li>
          );
        })}
      </ul>
    </>
  );
}
