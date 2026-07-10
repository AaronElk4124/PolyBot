"use client";
import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  Cell,
  LabelList,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { usd, pnlClass } from "@/lib/format";

const GOOD = "#0ca30c";
const BAD = "#d03b3b";
const AXIS = "#8b93a7";

interface Bucketed {
  name: string;
  pnl: number;
  n: number;
  wins: number;
}

function bucketBy(resolved: any[], keyFn: (r: any) => string): Bucketed[] {
  const map = new Map<string, Bucketed>();
  for (const r of resolved) {
    const k = keyFn(r);
    const b = map.get(k) ?? { name: k, pnl: 0, n: 0, wins: 0 };
    b.pnl += r.pnl_usdc ?? 0;
    b.n += 1;
    b.wins += r.won ? 1 : 0;
    map.set(k, b);
  }
  return [...map.values()].map((b) => ({ ...b, pnl: +b.pnl.toFixed(2) }));
}

function BreakdownTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload as Bucketed;
  return (
    <div className="rounded-lg bg-popover ring-1 ring-border px-3 py-2 text-xs shadow-xl">
      <div className="font-semibold">{p.name}</div>
      <div className={`tnum font-bold ${pnlClass(p.pnl)}`}>{usd(p.pnl, true)}</div>
      <div className="text-muted-foreground tnum">
        {p.n} trades · {p.wins}W {p.n - p.wins}L (
        {p.n ? Math.round((p.wins / p.n) * 100) : 0}% win)
      </div>
    </div>
  );
}

function Breakdown({ title, data }: { title: string; data: Bucketed[] }) {
  const h = Math.max(120, data.length * 34 + 30);
  return (
    <div className="rounded-xl ring-1 ring-border bg-card p-4">
      <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
        {title}
      </div>
      <div style={{ height: h }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 0, right: 56, bottom: 0, left: 8 }}
          >
            <XAxis type="number" hide />
            <YAxis
              type="category"
              dataKey="name"
              width={120}
              stroke="transparent"
              tick={{ fill: AXIS, fontSize: 11 }}
              tickLine={false}
            />
            <ReferenceLine x={0} stroke="#3a4353" />
            <Tooltip content={<BreakdownTooltip />} cursor={{ fill: "rgba(255,255,255,0.03)" }} />
            <Bar dataKey="pnl" barSize={16} radius={[0, 4, 4, 0]}>
              {data.map((d, i) => (
                <Cell key={i} fill={d.pnl >= 0 ? GOOD : BAD} />
              ))}
              <LabelList
                dataKey="pnl"
                position="right"
                formatter={(v: any) => usd(typeof v === "number" ? v : Number(v), true)}
                style={{ fill: "#c6cbd6", fontSize: 11, fontVariantNumeric: "tabular-nums" }}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export default function Analytics({
  resolved,
  positions = {},
  marks = {},
}: {
  resolved: any[];
  positions?: Record<string, any>;
  marks?: Record<string, any>;
}) {
  const [includeOpen, setIncludeOpen] = useState(true);

  // Open positions, marked to market, shaped like resolved records so every
  // breakdown can include them. Their P&L is provisional until they close.
  const records = useMemo(() => {
    if (!includeOpen) return resolved;
    const open = Object.entries(positions).map(([id, p]: [string, any]) => {
      const upnl = marks?.[id]?.unrealized_pnl ?? p.cur_unrealized_pnl ?? 0;
      return {
        question: p.question,
        bet_outcome: p.outcome,
        market_price: p.market_price,
        pnl_usdc: upnl,
        fees_usdc: p.fee_usdc ?? 0,
        strategy: p.strategy ?? "scalp",
        opened_at: p.opened_at,
        resolved_outcome: "open",
        close_reason: "open (mark-to-market)",
        won: upnl > 0,
      };
    });
    return [...resolved, ...open];
  }, [resolved, positions, marks, includeOpen]);
  const byReason = useMemo(
    () =>
      bucketBy(records, (r) =>
        r.resolved_outcome === "open"
          ? "open (mark-to-mkt)"
          : r.resolved_outcome === "early-close"
          ? (r.close_reason ?? "early close").split(":")[0]
          : "held to settlement"
      ).sort((a, b) => a.pnl - b.pnl),
    [records]
  );

  const byBand = useMemo(() => {
    const bands: [number, number, string][] = [
      [0, 0.4, "< 40¢ entry"],
      [0.4, 0.6, "40–60¢ entry"],
      [0.6, 0.8, "60–80¢ entry"],
      [0.8, 1.01, "80¢+ entry"],
    ];
    const out = bucketBy(records, (r) => {
      const p = r.market_price ?? 0;
      return bands.find(([lo, hi]) => p >= lo && p < hi)?.[2] ?? "?";
    });
    return bands.map(([, , label]) => out.find((o) => o.name === label)).filter(Boolean) as Bucketed[];
  }, [records]);

  const bySide = useMemo(
    () => bucketBy(records, (r) => (r.bet_outcome === "YES" ? "YES bets" : "NO bets")),
    [records]  );

  const byStrategy = useMemo(
    () =>
      bucketBy(records, (r) => r.strategy ?? "untagged").sort(
        (a, b) => a.pnl - b.pnl
      ),
    [records]  );

  const byCity = useMemo(
    () =>
      bucketBy(records, (r) => {
        const m = r.question?.match(/in (.+?) be /i);
        return m ? m[1] : "other";
      })
        .sort((a, b) => a.pnl - b.pnl)
        .slice(0, 10),
    [records]  );

  // Entry hour (viewer's local time) → P&L. Finds the golden trading windows.
  const byEntryHour = useMemo(() => {
    const out = bucketBy(
      records.filter((r: any) => r.logged_at || r.opened_at),
      (r: any) => {
        const h = new Date(r.logged_at ?? r.opened_at).getHours();
        return `${String(h).padStart(2, "0")}:00`;
      }
    );
    return out.sort((a, b) => a.name.localeCompare(b.name));
  }, [records]);

  if (!records.length) {
    return (
      <div className="flex items-center justify-center h-40 text-sm text-muted-foreground">
        Analytics appear once there are trades to analyze.
      </div>
    );
  }

  const nOpen = includeOpen ? Object.keys(positions).length : 0;

  return (
    <div className="flex flex-col gap-2 overflow-auto pb-2">
      <div className="flex items-center gap-2">
        <button
          onClick={() => setIncludeOpen(!includeOpen)}
          className={`text-[11px] px-2.5 py-1 rounded-full ring-1 font-semibold transition-colors cursor-pointer ${
            includeOpen
              ? "bg-[#3987e5]/15 text-[#6da7ec] ring-[#3987e5]/35"
              : "bg-secondary text-muted-foreground ring-border hover:text-foreground"
          }`}
        >
          {includeOpen ? "✓ " : ""}include open positions (mark-to-market)
        </button>
        <span className="text-[11px] text-muted-foreground tnum">
          {resolved.length} resolved{nOpen ? ` + ${nOpen} open` : ""} — open P&L is
          provisional until close
        </span>
      </div>
      <div className="grid md:grid-cols-2 gap-3">
        <Breakdown title="P&L by strategy — which playbook earns" data={byStrategy} />
        <Breakdown title="P&L by exit type" data={byReason} />
        <Breakdown title="P&L by entry price" data={byBand} />
        <Breakdown title="P&L by side" data={bySide} />
        <Breakdown title="P&L by city (worst → best)" data={byCity} />
        <Breakdown title="P&L by entry hour (your local time)" data={byEntryHour} />
      </div>
    </div>
  );
}
