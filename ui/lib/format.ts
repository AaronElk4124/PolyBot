/** Shared formatting helpers for the trading dashboard. */

export const usd = (v: number | null | undefined, signed = false): string => {
  if (v == null || Number.isNaN(v)) return "—";
  const sign = v > 0 && signed ? "+" : v < 0 ? "−" : "";
  return `${sign}$${Math.abs(v).toFixed(2)}`;
};

export const cents = (p: number | null | undefined): string =>
  p == null || Number.isNaN(p) ? "—" : `${Math.round(p * 100)}¢`;

export const pct = (v: number | null | undefined, signed = false): string => {
  if (v == null || Number.isNaN(v)) return "—";
  const sign = v > 0 && signed ? "+" : v < 0 ? "−" : "";
  return `${sign}${Math.abs(v * 100).toFixed(1)}%`;
};

export const pnlClass = (v: number | null | undefined): string =>
  v == null ? "text-muted-foreground" : v >= 0 ? "text-[#0ca30c]" : "text-[#e05252]";

/** "Will the highest temperature in Austin be between 92-93°F on July 14, 2026?" → parts */
export function parseQuestion(q: string) {
  const m = q?.match(
    /Will the (highest|lowest) temperature in (.+?) be (.+?) on (.+?)\??$/i
  );
  if (!m) return { kind: "", city: q ?? "", bucket: "", date: "" };
  return {
    kind: m[1].toLowerCase() === "highest" ? "High" : "Low",
    city: m[2],
    bucket: m[3].replace("between ", ""),
    date: m[4],
  };
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 0) return "now";
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
