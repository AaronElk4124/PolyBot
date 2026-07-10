"use client";
import { useMemo, useState } from "react";
import { cents, usd, pnlClass, parseQuestion } from "@/lib/format";
import StrategyBadge from "./StrategyBadge";

type Filter = "all" | "wins" | "losses" | "early" | "settled";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "wins", label: "Wins" },
  { key: "losses", label: "Losses" },
  { key: "early", label: "Early closes" },
  { key: "settled", label: "Settled" },
];

export default function HistoryTable({ resolved }: { resolved: any[] }) {
  const [filter, setFilter] = useState<Filter>("all");

  const rows = useMemo(() => {
    const sorted = [...resolved].sort((a, b) =>
      (b.resolved_at ?? "").localeCompare(a.resolved_at ?? "")
    );
    switch (filter) {
      case "wins":
        return sorted.filter((r) => r.won);
      case "losses":
        return sorted.filter((r) => !r.won);
      case "early":
        return sorted.filter((r) => r.resolved_outcome === "early-close");
      case "settled":
        return sorted.filter((r) => r.resolved_outcome !== "early-close");
      default:
        return sorted;
    }
  }, [resolved, filter]);

  const filteredPnl = rows.reduce((s, r) => s + (r.pnl_usdc ?? 0), 0);

  if (!resolved.length) {
    return (
      <div className="flex items-center justify-center h-40 text-sm text-muted-foreground">
        No resolved trades yet.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2 h-full min-h-0">
      <div className="flex items-center gap-1.5 flex-wrap">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            className={`text-[11px] px-2.5 py-1 rounded-full ring-1 font-semibold transition-colors cursor-pointer ${
              filter === f.key
                ? "bg-[#3987e5]/15 text-[#6da7ec] ring-[#3987e5]/35"
                : "bg-secondary text-muted-foreground ring-border hover:text-foreground"
            }`}
          >
            {f.label}
          </button>
        ))}
        <span className="text-[11px] text-muted-foreground ml-1 tnum">
          {rows.length} trades ·{" "}
          <b className={pnlClass(filteredPnl)}>{usd(filteredPnl, true)}</b>
        </span>
      </div>

      <div className="rounded-xl ring-1 ring-border bg-card overflow-auto flex-1 min-h-0">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-card z-10">
            <tr className="text-left text-muted-foreground border-b border-border">
              <th className="px-3 py-2 font-medium">Closed</th>
              <th className="px-2 py-2 font-medium">Market</th>
              <th className="px-2 py-2 font-medium">Side</th>
              <th className="px-2 py-2 font-medium">Strategy</th>
              <th className="px-2 py-2 font-medium text-right">Entry</th>
              <th className="px-2 py-2 font-medium text-right">Exit</th>
              <th className="px-2 py-2 font-medium text-right">Stake</th>
              <th className="px-2 py-2 font-medium text-right">Fees</th>
              <th className="px-2 py-2 font-medium text-right">P&L</th>
              <th className="px-3 py-2 font-medium">Why closed</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const q = parseQuestion(r.question ?? "");
              const early = r.resolved_outcome === "early-close";
              const exit = early
                ? r.exit_price
                : r.won
                ? 1
                : 0;
              return (
                <tr
                  key={`${r.market_id}-${r.logged_at}-${i}`}
                  className="border-b border-border/60 hover:bg-accent/40"
                >
                  <td className="px-3 py-2 text-muted-foreground whitespace-nowrap tnum">
                    {r.resolved_at
                      ? new Date(r.resolved_at).toLocaleString([], {
                          month: "short",
                          day: "numeric",
                          hour: "2-digit",
                          minute: "2-digit",
                        })
                      : "—"}
                  </td>
                  <td className="px-2 py-2">
                    <div className="capitalize font-semibold truncate max-w-56">
                      {q.city}{" "}
                      <span className="text-muted-foreground font-normal">{q.kind}</span>{" "}
                      {q.bucket}
                    </div>
                    <div className="text-[10px] text-muted-foreground">{q.date}</div>
                  </td>
                  <td className="px-2 py-2">
                    <span
                      className={`inline-flex px-1.5 py-0.5 rounded text-[10px] font-bold ${
                        r.bet_outcome === "YES"
                          ? "bg-[#0ca30c]/15 text-[#2fc32f]"
                          : "bg-[#d03b3b]/15 text-[#e05252]"
                      }`}
                    >
                      {r.bet_outcome}
                    </span>
                  </td>
                  <td className="px-2 py-2">
                    <StrategyBadge strategy={r.strategy} />
                  </td>
                  <td className="px-2 py-2 text-right tnum">{cents(r.market_price)}</td>
                  <td className="px-2 py-2 text-right tnum">{cents(exit)}</td>
                  <td className="px-2 py-2 text-right tnum">
                    ${(r.stake_usdc ?? 0).toFixed(0)}
                  </td>
                  <td className="px-2 py-2 text-right tnum text-muted-foreground">
                    {r.fees_usdc != null ? usd(-r.fees_usdc) : "—"}
                  </td>
                  <td
                    className={`px-2 py-2 text-right tnum font-bold ${pnlClass(
                      r.pnl_usdc
                    )}`}
                  >
                    {usd(r.pnl_usdc, true)}
                  </td>
                  <td className="px-3 py-2 text-muted-foreground max-w-72">
                    <span className="line-clamp-2">
                      {early ? r.close_reason : `settled ${r.resolved_outcome}`}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
