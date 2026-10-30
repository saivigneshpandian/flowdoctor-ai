"use client";

import { RefreshCw, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { incidentBucket, IncidentTable, type IncidentFilter } from "@/components/incident-table";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, errorMessage } from "@/lib/api";
import type { Incident } from "@/lib/types";
import { cn } from "@/lib/utils";

const FILTERS: { value: IncidentFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "fixed", label: "Fixed" },
  { value: "rejected", label: "Rejected" },
  { value: "open", label: "Open" },
  { value: "manual", label: "Manual" },
];

export default function IncidentsPage() {
  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<IncidentFilter>("all");

  const load = useCallback(async () => {
    setError(null);
    try {
      setIncidents(await api.getIncidents());
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }, []);

  useEffect(() => {
    const initial = setTimeout(() => void load(), 0);
    return () => clearTimeout(initial);
  }, [load]);

  const count = (value: IncidentFilter) =>
    !incidents
      ? null
      : value === "all"
        ? incidents.length
        : incidents.filter((incident) => incidentBucket(incident) === value).length;

  const visible =
    incidents?.filter((incident) => filter === "all" || incidentBucket(incident) === filter) ?? [];

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-6xl px-5 py-7 md:px-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Incidents</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Every failure, its diagnosis, and what was decided.
            </p>
          </div>
          <div role="group" aria-label="Filter incidents" className="flex gap-1 rounded-lg border bg-panel p-1">
            {FILTERS.map(({ value, label }) => {
              const total = count(value);
              return (
                <button
                  key={value}
                  type="button"
                  aria-pressed={filter === value}
                  onClick={() => setFilter(value)}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-sm text-muted-foreground transition-colors outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring",
                    filter === value && "bg-secondary font-medium text-foreground",
                  )}
                >
                  {label}
                  {total !== null && <span className="ml-1.5 font-mono text-xs opacity-70">{total}</span>}
                </button>
              );
            })}
          </div>
        </div>

        <div className="mt-6">
          {incidents === null && !error && (
            <div aria-label="Loading incidents" className="grid gap-2">
              {[0, 1, 2, 3].map((index) => (
                <Skeleton key={index} className="h-16 w-full rounded-xl bg-secondary/70" />
              ))}
            </div>
          )}

          {error && (
            <div role="alert" className="rounded-xl border border-fail/30 bg-fail/10 p-5">
              <p className="flex items-center gap-2 font-medium text-fail">
                <TriangleAlert aria-hidden className="size-4" />
                Couldn&apos;t load incidents
              </p>
              <p className="mt-1 text-sm text-muted-foreground">{error}</p>
              <Button type="button" variant="outline" className="mt-3 h-9 gap-2" onClick={() => void load()}>
                <RefreshCw aria-hidden className="size-4" /> Retry
              </Button>
            </div>
          )}

          {incidents && !error && visible.length === 0 && (
            <div className="rounded-xl border border-dashed bg-card/50 px-6 py-14 text-center">
              <p className="font-medium">
                {incidents.length === 0 ? "No incidents yet" : `No ${filter} incidents`}
              </p>
              <p className="mt-1 text-sm text-muted-foreground">
                {incidents.length === 0
                  ? "Failures will appear here once FlowDoctor records them."
                  : "Try a different filter."}
              </p>
            </div>
          )}

          {incidents && !error && visible.length > 0 && <IncidentTable incidents={visible} />}
        </div>
      </div>
    </div>
  );
}
