export function relativeTime(iso: string | null, now = Date.now()): string {
  if (!iso) return "Never run";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "Unknown";

  const minutes = Math.floor((now - then) / 60_000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

const timestamp = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

export function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "Unknown" : timestamp.format(date);
}
