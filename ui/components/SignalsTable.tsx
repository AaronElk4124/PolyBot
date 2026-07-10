"use client";
import { Fragment, useMemo, useState } from "react";
import { cents, pct, parseQuestion } from "@/lib/format";
import StrategyBadge from "./StrategyBadge";

function SideBadge({ side }: { side: string }) {
  const yes = side === "YES";
  return (
    <span
      className={`inline-flex px-1.5 py-0.5 rounded text-[10px] font-bold ${
        yes
          ? "bg-[#0ca30c]/15 text-[#2fc32f] ring-1 ring-[#0ca30c]/30"
          : "bg-[#d03b3b]/15 text-[#e05252] ring-1 ring-[#d03b3b]/30"
      }`}
    >
      {side}
    </span>
  );
}

function RegimeBadge({ regime }: { regime: string }) {
  const map: Record<string, string> = {
    intraday: "bg-[#9085e9]/15 text-[#a89ef0] ring-[#9085e9]/30",
    sameday: "bg-[#3987e5]/15 text-[#6da7ec] ring-[#3987e5]/30",
    nextday: "bg-secondary text-muted-foreground ring-border",
  };
  return (
    <span
      className={`inline-flex px-1.5 py-0.5 rounded text-[10px] font-semibold ring-1 ${
        map[regime] ?? map.nextday
      }`}
    >
      {regime}
    </span>
  );
}

export default function SignalsTable({
  signals,
  heldIds = new Set<string>(),
}: {
  signals: any[];
  heldIds?: Set<string>;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const [onlyBettable, setOnlyBettable] = useState(false);

  const sorted = useMemo(() => {
    const list = onlyBettable ? signals.filter((s) => s.bettable) : [...signals];
    return list.sort((a, b) => {
      if (a.bettable !== b.bettable) return a.bettable ? -1 : 1;
      return Math.abs(b.edge ?? 0) - Math.abs(a.edge ?? 0);
    });
  }, [signals, onlyBettable]);

  if (!signals.length) {
    return (
      <div className="flex items-center justify-center h-40 text-sm text-muted-foreground">
        No edges found this scan — the market agrees with the forecasts right now
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2 h-full min-h-0">
      <div className="flex items-center gap-2">
        <button
          onClick={() => setOnlyBettable(!onlyBettable)}
          className={`text-[11px] px-2.5 py-1 rounded-full ring-1 font-semibold transition-colors cursor-pointer ${
            onlyBettable
              ? "bg-[#0ca30c]/15 text-[#2fc32f] ring-[#0ca30c]/30"
              : "bg-secondary text-muted-foreground ring-border hover:text-foreground"
          }`}
        >
          {onlyBettable ? "✓ " : ""}bettable only
        </button>
        <span className="text-[11px] text-muted-foreground">
          {signals.filter((s) => s.bettable).length} bettable ·{" "}
          {signals.length} signals · click a row for the full reasoning
        </span>
      </div>

      <div className="rounded-xl ring-1 ring-border bg-card overflow-auto flex-1 min-h-0">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-card z-10">
            <tr className="text-left text-muted-foreground border-b border-border">
              <th className="px-3 py-2 font-medium">Market</th>
              <th className="px-2 py-2 font-medium">Side</th>
              <th className="px-2 py-2 font-medium text-right">Price</th>
              <th className="px-2 py-2 font-medium text-right">Our est</th>
              <th className="px-2 py-2 font-medium text-right">Edge*</th>
              <th className="px-2 py-2 font-medium text-right">Stake</th>
              <th className="px-2 py-2 font-medium">Forecast</th>
              <th className="px-2 py-2 font-medium">Live obs</th>
              <th className="px-3 py-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((s) => {
              const q = parseQuestion(s.question);
              const held = heldIds.has(s.market_id);
              const isOpen = open === s.market_id;
              const fcVal = q.kind === "Low" ? s.forecast_low_f : s.forecast_high_f;
              const fcStd = q.kind === "Low" ? s.forecast_low_f_std : s.forecast_high_f_std;
              return (
                <Fragment key={s.market_id}>
                  <tr
                    onClick={() => setOpen(isOpen ? null : s.market_id)}
                    className={`border-b border-border/60 cursor-pointer transition-colors hover:bg-accent/40 ${
                      s.bettable ? "" : "opacity-55"
                    }`}
                  >
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-2 min-w-0">
                        {held && (
                          <span
                            title="You hold a position here"
                            className="w-1.5 h-1.5 rounded-full bg-[#e5a940] shrink-0"
                          />
                        )}
                        <div className="min-w-0">
                          <div className="font-semibold text-foreground truncate capitalize">
                            {q.city}{" "}
                            <span className="text-muted-foreground font-normal">
                              {q.kind}
                            </span>{" "}
                            <span className="text-foreground/90">{q.bucket}</span>
                          </div>
                          <div className="flex items-center gap-1.5 mt-0.5 text-muted-foreground">
                            <RegimeBadge regime={s.regime} />
                            <StrategyBadge strategy={s.strategy} />
                            <span>{q.date}</span>
                            {s.metar_station && <span>· {s.metar_station}</span>}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="px-2 py-2">
                      <SideBadge side={s.outcome} />
                    </td>
                    <td className="px-2 py-2 text-right tnum font-semibold">
                      {cents(s.market_price)}
                    </td>
                    <td className="px-2 py-2 text-right tnum">
                      {pct(s.outcome === "NO" ? 1 - s.our_prob : s.our_prob)}
                    </td>
                    <td
                      className={`px-2 py-2 text-right tnum font-bold ${
                        s.edge >= 0.1
                          ? "text-[#2fc32f]"
                          : s.edge >= 0.04
                          ? "text-[#e5a940]"
                          : "text-muted-foreground"
                      }`}
                    >
                      {pct(s.edge, true)}
                    </td>
                    <td className="px-2 py-2 text-right tnum">
                      ${(s.kelly_stake ?? 0).toFixed(0)}
                    </td>
                    <td className="px-2 py-2 tnum whitespace-nowrap">
                      {fcVal != null ? (
                        <>
                          <b>{fcVal.toFixed(1)}°F</b>
                          <span className="text-muted-foreground">
                            {" "}
                            ±{(fcStd ?? 0).toFixed(1)}
                          </span>
                          {s.sources_agree ? (
                            <span className="text-[#2fc32f]" title="Sources agree">
                              {" "}
                              ✓
                            </span>
                          ) : (
                            <span className="text-[#e5a940]" title="Sources disagree">
                              {" "}
                              ✗
                            </span>
                          )}
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-2 py-2 tnum whitespace-nowrap">
                      {s.metar_temp_f != null ? (
                        <>
                          {s.metar_temp_f.toFixed(0)}°F
                          <span className="text-muted-foreground">
                            {" "}
                            {s.metar_age_min != null ? `${s.metar_age_min}m` : ""}
                          </span>
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-3 py-2">
                      {s.bettable ? (
                        <span className="inline-flex items-center gap-1 text-[#2fc32f] font-bold">
                          ● BET
                        </span>
                      ) : (
                        <span className="text-muted-foreground">{s.skip_reason}</span>
                      )}
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="border-b border-border/60 bg-secondary/30">
                      <td colSpan={9} className="px-4 py-3">
                        <div className="grid md:grid-cols-2 gap-x-8 gap-y-1 text-[11px]">
                          <div className="text-muted-foreground md:col-span-2 mb-1">
                            <b className="text-foreground">{s.question}</b>
                          </div>
                          <div>
                            <span className="text-muted-foreground">Reasoning: </span>
                            {s.reason || "—"}
                          </div>
                          <div className="tnum">
                            <span className="text-muted-foreground">Sources: </span>
                            NWS {s.nws_high_f != null ? `${s.nws_high_f}°F` : "—"} / OM{" "}
                            {s.om_high_f != null ? `${s.om_high_f.toFixed(1)}°F` : "—"} (high) ·
                            NWS {s.nws_low_f != null ? `${s.nws_low_f}°F` : "—"} / OM{" "}
                            {s.om_low_f != null ? `${s.om_low_f.toFixed(1)}°F` : "—"} (low)
                          </div>
                          <div className="tnum">
                            <span className="text-muted-foreground">Market: </span>
                            spread {cents(s.spread)} · 24h vol{" "}
                            {(s.volume_24h ?? 0).toLocaleString()} · fee {cents(s.fee)}/contract
                          </div>
                          {s.metar_raw && (
                            <div className="font-mono text-muted-foreground truncate">
                              {s.metar_raw}
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="text-[10px] text-muted-foreground px-1">
        *Edge is net of the ask price and Kalshi's trading fee.
      </div>
    </div>
  );
}
