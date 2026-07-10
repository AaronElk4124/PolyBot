"use client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

function fmtTemp(f: number | null | undefined, q: string) {
  if (f == null) return "—";
  const isC = q?.includes("°C");
  return isC ? `${((f - 32) * 5 / 9).toFixed(1)}°C` : `${f.toFixed(0)}°F`;
}

function winStatus(pos: any, signals: any[]) {
  const q       = pos.question ?? "";
  const isLow   = q.toLowerCase().includes("lowest");
  const isC     = q.includes("°C");
  const outcome = pos.outcome;

  // Find latest forecast for this market
  const sig = signals.find(
    (s: any) => s.market_id === pos.market_id || s.question === pos.question
  );
  const fc_val_f = sig ? (isLow ? sig.forecast_low_f : sig.forecast_high_f) : null;
  if (fc_val_f == null) return null;

  const fc = isC ? (fc_val_f - 32) * 5 / 9 : fc_val_f;

  // Extract bucket temps (only temperature numbers)
  const bucket = q.toLowerCase().includes(" be ")
    ? q.slice(q.toLowerCase().indexOf(" be ") + 4).replace(/ on .+/, "")
    : "";
  const nums = [...bucket.matchAll(/([\d.]+)\s*°/g)].map(m => parseFloat(m[1]));
  if (!nums.length) return null;

  const lo = nums[0], hi = nums[nums.length - 1];
  let inBucket: boolean;

  if (bucket.includes("or higher") || bucket.includes("or above")) {
    inBucket = fc >= lo;
  } else if (bucket.includes("or below") || bucket.includes("<")) {
    inBucket = fc <= hi;
  } else {
    inBucket = fc >= lo - 1 && fc <= hi + 1;
  }

  const winning = outcome === "YES" ? inBucket : !inBucket;
  return winning;
}

export default function Positions({ posMap, signals }: { posMap: Record<string, any>; signals: any[] }) {
  const positions = Object.entries(posMap);

  return (
    <div className="flex flex-col gap-3">
      <h2 className="text-xs uppercase tracking-widest text-slate-500 font-semibold px-1">
        Open Positions <span className="text-slate-600">({positions.length})</span>
      </h2>

      {positions.length === 0 && (
        <div className="text-slate-600 text-sm text-center py-8 border border-slate-800 rounded-lg">
          No open positions
        </div>
      )}

      {positions.map(([id, pos]) => {
        const q        = pos.question ?? "";
        const isLow    = q.toLowerCase().includes("lowest");
        const isC      = q.includes("°C");
        const city     = q.match(/in (.+?) be /i)?.[1] ?? "";
        const bucket   = q.toLowerCase().includes(" be ")
          ? q.slice(q.toLowerCase().indexOf(" be ") + 4).replace(/\?.*/, "").replace(/ on .+/, "").trim()
          : q.slice(0, 45);
        const sig      = signals.find((s: any) => s.market_id === id || s.question === q);
        const fc_val_f = sig ? (isLow ? sig.forecast_low_f : sig.forecast_high_f) : null;
        const live_f   = sig?.metar_temp_f;
        const targetDate = pos.target_date;
        const isToday  = targetDate === new Date().toISOString().slice(0, 10);
        const winning  = winStatus(pos, signals);

        return (
          <Card key={id} className="bg-[#141824] border-slate-800">
            <CardHeader className="pb-2 pt-3 px-4">
              <div className="flex items-start justify-between gap-2">
                <div className="flex-1 min-w-0">
                  <div className="text-xs text-slate-400 font-medium">{city}</div>
                  <div className="text-xs text-slate-500 mt-0.5 leading-tight">{bucket}</div>
                </div>
                <div className="flex flex-col items-end gap-1 shrink-0">
                  <Badge variant="outline" className={
                    pos.outcome === "YES"
                      ? "border-emerald-500/40 text-emerald-400 bg-emerald-500/10 text-xs"
                      : "border-red-500/40 text-red-400 bg-red-500/10 text-xs"
                  }>
                    {pos.outcome === "YES" ? "▲ YES" : "▼ NO"}
                  </Badge>
                  {winning !== null && (
                    <span className={`text-[10px] font-bold ${winning ? "text-emerald-400" : "text-red-400"}`}>
                      {winning ? "● WINNING" : "● LOSING"}
                    </span>
                  )}
                </div>
              </div>
            </CardHeader>
            <CardContent className="px-4 pb-3">
              <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
                <Stat label="Entry" value={`${((pos.market_price ?? 0) * 100).toFixed(1)}¢`} />
                <Stat label="Stake" value={`$${pos.stake_usdc ?? pos.stake ?? 0}`} />
                {isToday && live_f != null ? (
                  <Stat label="Live Now" value={fmtTemp(live_f, q)} color="text-emerald-400" />
                ) : (
                  <Stat label={`Fcst ${isLow ? "Low" : "High"}`} value={fmtTemp(fc_val_f, q)} />
                )}
                {isToday && fc_val_f != null && (
                  <Stat label="Fcst" value={fmtTemp(fc_val_f, q)} />
                )}
                <Stat label="Resolves" value={targetDate ? new Date(targetDate + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "—"} />
              </div>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <div className="text-[10px] text-slate-500 uppercase tracking-wider">{label}</div>
      <div className={`font-medium text-slate-200 ${color ?? ""}`}>{value}</div>
    </div>
  );
}
