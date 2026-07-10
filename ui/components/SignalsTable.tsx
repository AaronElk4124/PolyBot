"use client";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const CITY_FLAG: Record<string, string> = {
  "los angeles": "🇺🇸", "new york city": "🇺🇸", "chicago": "🇺🇸",
  "houston": "🇺🇸", "miami": "🇺🇸", "dallas": "🇺🇸", "phoenix": "🇺🇸",
  "seattle": "🇺🇸", "denver": "🇺🇸", "atlanta": "🇺🇸", "boston": "🇺🇸",
  "washington": "🇺🇸", "philadelphia": "🇺🇸", "san francisco": "🇺🇸",
  "las vegas": "🇺🇸", "austin": "🇺🇸", "san antonio": "🇺🇸",
  "new orleans": "🇺🇸", "tampa": "🇺🇸", "minneapolis": "🇺🇸",
  "oklahoma city": "🇺🇸", "salt lake city": "🇺🇸", "portland": "🇺🇸",
};

function flag(city: string) {
  return CITY_FLAG[city.toLowerCase()] ?? "🌍";
}

function edgeColor(edge: number) {
  if (edge >= 0.3) return "text-emerald-300";
  if (edge >= 0.15) return "text-emerald-400";
  return "text-yellow-400";
}

function fmtTemp(f: number | null | undefined, isC: boolean) {
  if (f == null) return "—";
  return isC ? `${((f - 32) * 5 / 9).toFixed(1)}°C` : `${f.toFixed(0)}°F`;
}

type SortKey = "edge" | "market_price" | "our_prob" | "kelly_stake" | "city";

export default function SignalsTable({ signals }: { signals: any[] }) {
  const [sortKey, setSortKey] = useState<SortKey>("edge");
  const [sortAsc, setSortAsc] = useState(false);

  const sorted = [...signals].sort((a, b) => {
    const av = sortKey === "city" ? a.forecast_city ?? "" : (a[sortKey] ?? 0);
    const bv = sortKey === "city" ? b.forecast_city ?? "" : (b[sortKey] ?? 0);
    return sortAsc
      ? typeof av === "string" ? av.localeCompare(bv) : av - bv
      : typeof av === "string" ? bv.localeCompare(av) : bv - av;
  });

  const toggle = (key: SortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(false); }
  };

  const Th = ({ k, children }: { k: SortKey; children: React.ReactNode }) => (
    <TableHead
      className="cursor-pointer select-none text-slate-400 hover:text-slate-200 whitespace-nowrap text-xs"
      onClick={() => toggle(k)}
    >
      {children} {sortKey === k ? (sortAsc ? "↑" : "↓") : ""}
    </TableHead>
  );

  if (!signals.length) {
    return (
      <div className="flex items-center justify-center h-40 text-slate-500 text-sm">
        No signals this scan — bot runs hourly
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-slate-800 overflow-auto h-full bg-[#0d1117]">
      <Table>
        <TableHeader className="sticky top-0 bg-[#0d1117] z-10">
          <TableRow className="border-slate-800 hover:bg-transparent">
            <TableHead className="text-slate-400 text-xs w-8"></TableHead>
            <Th k="city">City</Th>
            <TableHead className="text-slate-400 text-xs">Bucket</TableHead>
            <TableHead className="text-slate-400 text-xs">H/L</TableHead>
            <TableHead className="text-slate-400 text-xs">Side</TableHead>
            <Th k="market_price">Mkt %</Th>
            <Th k="our_prob">Our %</Th>
            <Th k="edge">Edge</Th>
            <Th k="kelly_stake">Stake</Th>
            <TableHead className="text-slate-400 text-xs">Forecast</TableHead>
            <TableHead className="text-slate-400 text-xs">NWS</TableHead>
            <TableHead className="text-slate-400 text-xs">Open-Meteo</TableHead>
            <TableHead className="text-slate-400 text-xs">σ</TableHead>
            <TableHead className="text-slate-400 text-xs">Live METAR</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((s, i) => {
            const city   = s.forecast_city ?? "";
            const q      = s.question ?? "";
            const isLow  = q.toLowerCase().includes("lowest");
            const isC    = q.includes("°C") || q.includes("°c");
            const bucket = q.toLowerCase().includes(" be ")
              ? q.slice(q.toLowerCase().indexOf(" be ") + 4).replace(/\?.*/, "").replace(/ on .+/, "").trim()
              : q.slice(0, 40);
            const edge   = s.edge ?? 0;
            const metar  = s.metar_temp_f;
            const age    = s.metar_age_min;

            // Parse source-specific forecasts from reason string
            const nwsMatch  = s.reason?.match(/NWS=([\d.]+°[CF])/);
            const omMatch   = s.reason?.match(/OM=([\d.]+°[CF])/);
            const sigmaMatch = s.reason?.match(/±([\d.]+)°/);

            return (
              <TableRow key={i} className="border-slate-800 hover:bg-[#141824] transition-colors">
                <TableCell className="text-lg">{flag(city)}</TableCell>
                <TableCell className="font-medium text-slate-100 whitespace-nowrap">{city}</TableCell>
                <TableCell className="text-slate-300 text-xs max-w-[180px] truncate">{bucket}</TableCell>
                <TableCell>
                  <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${
                    isLow
                      ? "bg-blue-500/15 text-blue-400"
                      : "bg-orange-500/15 text-orange-400"
                  }`}>
                    {isLow ? "↓ LOW" : "↑ HIGH"}
                  </span>
                </TableCell>
                <TableCell>
                  <Badge variant="outline" className={
                    s.outcome === "YES"
                      ? "border-emerald-500/40 text-emerald-400 bg-emerald-500/10"
                      : "border-red-500/40 text-red-400 bg-red-500/10"
                  }>
                    {s.outcome === "YES" ? "▲ YES" : "▼ NO"}
                  </Badge>
                </TableCell>
                <TableCell className="text-slate-300 text-sm">{((s.market_price ?? 0) * 100).toFixed(1)}%</TableCell>
                <TableCell className="text-slate-300 text-sm">{((s.our_prob ?? 0) * 100).toFixed(1)}%</TableCell>
                <TableCell className={`font-bold text-sm ${edgeColor(edge)}`}>
                  {(edge * 100).toFixed(1)}%
                </TableCell>
                <TableCell className="text-slate-200 font-medium text-sm">${(s.kelly_stake ?? 0).toFixed(0)}</TableCell>
                <TableCell className="text-slate-300 text-sm font-mono">
                  {fmtTemp(isLow ? s.forecast_low_f : s.forecast_high_f, isC)}
                </TableCell>
                <TableCell className="text-slate-400 text-xs font-mono">
                  {nwsMatch ? nwsMatch[1] : "—"}
                </TableCell>
                <TableCell className="text-slate-400 text-xs font-mono">
                  {omMatch ? omMatch[1] : "—"}
                </TableCell>
                <TableCell className="text-slate-500 text-xs">
                  {sigmaMatch ? `±${sigmaMatch[1]}°` : "—"}
                </TableCell>
                <TableCell className="text-xs font-mono">
                  {metar != null ? (
                    <span className={age && age < 40 ? "text-emerald-400" : "text-yellow-400"}>
                      {metar.toFixed(0)}°F {age ? `(${Math.round(age)}m)` : ""}
                    </span>
                  ) : <span className="text-slate-600">—</span>}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
