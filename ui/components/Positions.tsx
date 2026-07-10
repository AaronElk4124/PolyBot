"use client";
import { cents, usd, pct, pnlClass, parseQuestion, timeAgo } from "@/lib/format";
import StrategyBadge from "./StrategyBadge";

interface Props {
  posMap: Record<string, any>;
  marks: Record<string, any>;
}

export default function Positions({ posMap, marks }: Props) {
  const entries = Object.entries(posMap).sort(
    ([, a], [, b]) => (b.opened_at ?? "").localeCompare(a.opened_at ?? "")
  );

  if (!entries.length) {
    return (
      <div className="rounded-xl ring-1 ring-border bg-card p-6 text-center text-sm text-muted-foreground">
        No open positions — the bot is waiting for a real edge.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2.5">
      {entries.map(([id, p]) => {
        const q = parseQuestion(p.question ?? "");
        const mark = marks?.[id];
        const cur = mark?.current_price ?? p.cur_price ?? null;
        const sellNow = mark?.sell_now_price ?? null;
        const wideSpread = cur != null && sellNow != null && cur - sellNow >= 0.02;
        const upnl = mark?.unrealized_pnl ?? p.cur_unrealized_pnl ?? null;
        const entry = p.market_price ?? 0;
        const gainPct = cur != null && entry > 0 ? cur / entry - 1 : null;
        const yes = p.outcome === "YES";
        const prob = p.cur_our_prob ?? null;
        const breaches = p.stop_breaches ?? 0;
        // progress of current price toward $1 payoff
        const progress = cur != null ? Math.max(0, Math.min(1, cur)) : entry;

        return (
          <div key={id} className="rounded-xl ring-1 ring-border bg-card p-3">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="text-[13px] font-semibold capitalize truncate">
                  {q.city} <span className="text-muted-foreground font-normal">{q.kind}</span>{" "}
                  {q.bucket}
                </div>
                <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground mt-0.5">
                  <StrategyBadge strategy={p.strategy} />
                  <span>
                    {q.date} · opened {timeAgo(p.opened_at)}
                  </span>
                </div>
              </div>
              <span
                className={`shrink-0 inline-flex px-1.5 py-0.5 rounded text-[10px] font-bold ${
                  yes
                    ? "bg-[#0ca30c]/15 text-[#2fc32f] ring-1 ring-[#0ca30c]/30"
                    : "bg-[#d03b3b]/15 text-[#e05252] ring-1 ring-[#d03b3b]/30"
                }`}
              >
                {p.outcome}
              </span>
            </div>

            <div className="mt-2.5 flex items-center justify-between text-xs tnum">
              <span className="text-muted-foreground">
                {cents(entry)} →{" "}
                <b
                  className="text-foreground cursor-help"
                  title={`fair value (mid). Full book: bid ${cents(sellNow)} · mid ${cents(cur)} · ask ${cents(mark?.ask_price)} · last trade ${cents(mark?.last_price)} (Kalshi's site shows the last trade)`}
                >
                  {cents(cur)}
                </b>
                {gainPct != null && (
                  <span className={`ml-1 ${pnlClass(gainPct)}`}>{pct(gainPct, true)}</span>
                )}
                {wideSpread && (
                  <span
                    className="ml-1.5 text-[#e5a940]"
                    title="Wide bid-ask spread — an instant sale would fill at the bid, below fair value"
                  >
                    · sell now {cents(sellNow)}
                  </span>
                )}
              </span>
              <span className={`font-bold ${pnlClass(upnl)}`}>{usd(upnl, true)}</span>
            </div>

            {/* entry → payout progress */}
            <div
              className="mt-1.5 h-1.5 rounded-full bg-secondary overflow-hidden"
              title={`current price ${cents(cur)} of 100¢ payout`}
            >
              <div
                className={`h-full rounded-full ${
                  gainPct != null && gainPct < 0 ? "bg-[#d03b3b]" : "bg-[#0ca30c]"
                }`}
                style={{ width: `${progress * 100}%` }}
              />
            </div>

            <div className="mt-2 flex items-center justify-between text-[11px] text-muted-foreground">
              <span className="tnum">
                stake ${(p.stake_usdc ?? 0).toFixed(0)}
                {prob != null && <> · model {pct(prob)} win</>}
              </span>
              {breaches > 0 && (
                <span className="text-[#e5a940] font-semibold">
                  ⚠ stop watch {breaches}
                </span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
