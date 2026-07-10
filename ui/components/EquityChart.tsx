"use client";
import { useMemo } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { usd } from "@/lib/format";

const BLUE = "#3987e5";
const GRID = "#232a37";
const AXIS = "#8b93a7";

function fmtTime(ts: number) {
  return new Date(ts).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function ChartTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-lg bg-popover ring-1 ring-border px-3 py-2 text-xs shadow-xl">
      <div className="text-muted-foreground">{fmtTime(p.ts)}</div>
      <div className="font-bold tnum text-sm mt-0.5">
        <span className={p.cum >= 0 ? "text-[#0ca30c]" : "text-[#e05252]"}>
          {usd(p.cum, true)}
        </span>{" "}
        <span className="text-muted-foreground font-normal">cumulative</span>
      </div>
      {p.trade && (
        <div className="mt-1 max-w-60 text-muted-foreground">
          <span className={p.pnl >= 0 ? "text-[#0ca30c]" : "text-[#e05252]"}>
            {usd(p.pnl, true)}
          </span>{" "}
          {p.trade}
        </div>
      )}
    </div>
  );
}

export default function EquityChart({ resolved }: { resolved: any[] }) {
  const data = useMemo(() => {
    const sorted = [...resolved]
      .filter((r) => r.resolved_at)
      .sort((a, b) => a.resolved_at.localeCompare(b.resolved_at));
    let cum = 0;
    return sorted.map((r) => {
      cum += r.pnl_usdc ?? 0;
      return {
        ts: new Date(r.resolved_at).getTime(),
        cum: +cum.toFixed(2),
        pnl: r.pnl_usdc ?? 0,
        trade: r.question?.slice(0, 60),
      };
    });
  }, [resolved]);

  if (data.length < 2) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
        Equity curve appears after a few resolved trades
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id="pbEquityFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={BLUE} stopOpacity={0.28} />
            <stop offset="100%" stopColor={BLUE} stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={GRID} strokeWidth={1} vertical={false} />
        <XAxis
          dataKey="ts"
          type="number"
          domain={["dataMin", "dataMax"]}
          scale="time"
          tickFormatter={(t) =>
            new Date(t).toLocaleString([], { month: "short", day: "numeric", hour: "numeric" })
          }
          stroke="transparent"
          tick={{ fill: AXIS, fontSize: 11 }}
          tickLine={false}
          minTickGap={70}
        />
        <YAxis
          tickFormatter={(v) => `$${v}`}
          stroke="transparent"
          tick={{ fill: AXIS, fontSize: 11 }}
          tickLine={false}
          width={52}
        />
        <ReferenceLine y={0} stroke="#3a4353" strokeWidth={1} />
        <Tooltip
          content={<ChartTooltip />}
          cursor={{ stroke: AXIS, strokeWidth: 1, strokeDasharray: "3 3" }}
        />
        <Area
          type="stepAfter"
          dataKey="cum"
          stroke={BLUE}
          strokeWidth={2}
          fill="url(#pbEquityFill)"
          dot={false}
          activeDot={{ r: 4, fill: BLUE, stroke: "#11151d", strokeWidth: 2 }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
