"use client";
import { usd, pct, pnlClass } from "@/lib/format";

interface Props {
  resolved: any[];
  positions: Record<string, any>;
  marks: Record<string, any>;
}

function Tile({
  label,
  value,
  sub,
  valueClass = "",
}: {
  label: string;
  value: string;
  sub?: React.ReactNode;
  valueClass?: string;
}) {
  return (
    <div className="rounded-xl bg-card ring-1 ring-border px-4 py-3 flex flex-col gap-0.5 min-w-0">
      <div className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium truncate">
        {label}
      </div>
      <div className={`text-xl font-bold tnum truncate ${valueClass}`}>{value}</div>
      {sub ? (
        <div className="text-[11px] text-muted-foreground truncate">{sub}</div>
      ) : null}
    </div>
  );
}

export default function StatTiles({ resolved, positions, marks }: Props) {
  const realized = resolved.reduce((s, r) => s + (r.pnl_usdc ?? 0), 0);
  // Fees = closed trades' round-trip fees + entry fees already sunk on OPEN positions
  const closedFees = resolved.reduce((s, r) => s + (r.fees_usdc ?? 0), 0);
  const openFees = Object.values(positions).reduce(
    (s: number, p: any) => s + (p.fee_usdc ?? 0),
    0
  );
  const fees = closedFees + openFees;
  const wins = resolved.filter((r) => r.won).length;
  const losses = resolved.length - wins;
  const winRate = resolved.length ? (wins / resolved.length) * 100 : null;

  const today = new Date().toISOString().slice(0, 10);
  const todayPnl = resolved
    .filter((r) => (r.resolved_at ?? "").slice(0, 10) === today)
    .reduce((s, r) => s + (r.pnl_usdc ?? 0), 0);
  const todayN = resolved.filter((r) => (r.resolved_at ?? "").slice(0, 10) === today).length;

  const posList = Object.entries(positions);
  const exposure = posList.reduce(
    (s, [, p]) => s + (p.stake_usdc ?? p.stake ?? 0),
    0
  );
  const unrealized = posList.reduce((s, [id, p]) => {
    const m = marks?.[id];
    if (m?.unrealized_pnl != null) return s + m.unrealized_pnl;
    if (p.cur_unrealized_pnl != null) return s + p.cur_unrealized_pnl;
    return s;
  }, 0);

  const total = realized + unrealized;

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
      <Tile
        label="Total P&L"
        value={usd(total, true)}
        valueClass={pnlClass(total)}
        sub={
          <>
            realized {usd(realized, true)} · open {usd(unrealized, true)}
          </>
        }
      />
      <Tile
        label="Today (realized)"
        value={usd(todayPnl, true)}
        valueClass={pnlClass(todayPnl)}
        sub={`${todayN} closed trade${todayN === 1 ? "" : "s"}`}
      />
      <Tile
        label="Unrealized"
        value={usd(unrealized, true)}
        valueClass={pnlClass(unrealized)}
        sub={`${posList.length} open position${posList.length === 1 ? "" : "s"}`}
      />
      <Tile
        label="Win rate"
        value={winRate == null ? "—" : `${winRate.toFixed(0)}%`}
        valueClass={
          winRate == null ? "" : winRate >= 50 ? "text-[#0ca30c]" : "text-[#e05252]"
        }
        sub={`${wins}W – ${losses}L (${resolved.length} resolved)`}
      />
      <Tile label="Open exposure" value={usd(exposure)} sub="cost basis at risk" />
      <Tile
        label="Fees modeled"
        value={usd(-fees)}
        valueClass="text-[#e5a940]"
        sub={`closed ${usd(-closedFees)} · open entries ${usd(-openFees)}`}
      />
    </div>
  );
}
