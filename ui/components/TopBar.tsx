"use client";
import { formatDistanceToNow } from "date-fns";

interface Props {
  scan: any;
  posCount: number;
  resolved: any[];
}

export default function TopBar({ scan, posCount, resolved }: Props) {
  const lastScan    = scan?.last_scan ? new Date(scan.last_scan) : null;
  const mode        = scan?.mode ?? "dry-run";
  const checked     = scan?.markets_checked ?? 0;
  const sigCount    = scan?.signals?.length ?? 0;
  const pnl         = resolved.reduce((s: number, r: any) => s + (r.pnl_usdc ?? 0), 0);
  const wins        = resolved.filter((r: any) => r.won).length;
  const winRate     = resolved.length ? Math.round((wins / resolved.length) * 100) : null;
  const isLive      = mode === "live";

  return (
    <div className="flex items-center gap-6 px-5 py-3 bg-[#0d1117] border-b border-slate-800 flex-shrink-0">
      {/* Logo */}
      <div className="flex items-center gap-2 mr-4">
        <span className="text-lg">🌤️</span>
        <span className="font-bold text-white tracking-tight">PolyBot</span>
      </div>

      {/* Mode badge */}
      <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold ${
        isLive ? "bg-emerald-500/15 text-emerald-400" : "bg-blue-500/15 text-blue-400"
      }`}>
        <span className={`w-1.5 h-1.5 rounded-full ${isLive ? "bg-emerald-400" : "bg-blue-400"} animate-pulse`} />
        {isLive ? "LIVE" : "DRY RUN"}
      </div>

      <div className="h-4 w-px bg-slate-700" />

      {/* Stats */}
      <Stat label="Last Scan" value={lastScan ? formatDistanceToNow(lastScan, { addSuffix: true }) : "—"} />
      <Stat label="Markets" value={checked.toLocaleString()} />
      <Stat label="Signals" value={sigCount.toString()} highlight={sigCount > 0} />
      <Stat label="Positions" value={`${posCount} / ${scan?.max_open ?? 5}`} />

      <div className="h-4 w-px bg-slate-700" />

      <Stat label="P&L" value={resolved.length ? `$${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}` : "—"}
            color={pnl >= 0 ? "text-emerald-400" : "text-red-400"} />
      {winRate !== null && (
        <Stat label="Win Rate" value={`${winRate}%`}
              color={winRate >= 50 ? "text-emerald-400" : "text-red-400"} />
      )}
    </div>
  );
}

function Stat({ label, value, highlight, color }: {
  label: string; value: string; highlight?: boolean; color?: string;
}) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-widest text-slate-500">{label}</span>
      <span className={`text-sm font-semibold ${color ?? (highlight ? "text-emerald-400" : "text-slate-200")}`}>
        {value}
      </span>
    </div>
  );
}
