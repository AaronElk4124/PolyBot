/**
 * GET /api/marks
 *
 * For each open position, fetches the current live Kalshi price and computes
 * unrealized (mark-to-market) P&L. Cached ~15s to avoid rate-limiting Kalshi.
 *
 * Returns: { [market_id]: { current_price, unrealized_pnl, unrealized_pct } }
 */

import { NextResponse } from "next/server";
import { readFileSync, existsSync } from "fs";
import { join } from "path";

const KALSHI = "https://api.elections.kalshi.com/trade-api/v2";

/** Kalshi trading fee in dollars: ceil-to-cent of 0.07·C·P·(1−P). */
function kalshiFee(price: number, contracts: number): number {
  if (price <= 0 || price >= 1) return 0;
  return Math.ceil(7 * contracts * price * (1 - price)) / 100;
}

// simple in-memory cache so 5s UI polling doesn't hammer Kalshi
let cache: { at: number; data: any } | null = null;
const TTL_MS = 15_000;

async function priceFor(
  marketId: string,
  outcome: string
): Promise<{ mid: number; sellNow: number; ask: number | null; last: number | null } | null> {
  try {
    const r = await fetch(`${KALSHI}/markets/${marketId}`, { cache: "no-store" });
    if (!r.ok) return null;
    const m = (await r.json()).market ?? {};
    // Kalshi returns prices as *_dollars strings already in 0-1 range (not cents)
    const num = (v: any) => (v != null && +v > 0 ? +v : null);
    const bid = outcome === "YES" ? num(m.yes_bid_dollars) : num(m.no_bid_dollars);
    const ask = outcome === "YES" ? num(m.yes_ask_dollars) : num(m.no_ask_dollars);
    // last_price_dollars is the YES last trade; for NO it's (1 - yes_last).
    // This is the price Kalshi's website displays most prominently.
    const yesLast = num(m.last_price_dollars);
    const sideLast = yesLast == null ? null : (outcome === "YES" ? yesLast : 1 - yesLast);
    // MID = fair value for display; BID = what selling right now actually gets.
    // Overnight books run 5-10¢ wide, so bid-only marking overstated losses.
    if (bid != null && ask != null)
      return { mid: (bid + ask) / 2, sellNow: bid, ask, last: sideLast };
    if (bid != null) return { mid: bid, sellNow: bid, ask, last: sideLast };
    if (sideLast != null) return { mid: sideLast, sellNow: sideLast, ask, last: sideLast };
    return ask != null ? { mid: ask * 0.5, sellNow: ask * 0.5, ask, last: sideLast } : null;
  } catch {
    return null;
  }
}

export async function GET() {
  if (cache && Date.now() - cache.at < TTL_MS) {
    return NextResponse.json(cache.data);
  }

  const file = join(process.cwd(), "data", "open_positions.json");
  if (!existsSync(file)) return NextResponse.json({});
  const positions = JSON.parse(readFileSync(file, "utf-8"));

  const marks: Record<string, any> = {};
  await Promise.all(
    Object.entries(positions).map(async ([id, pos]: [string, any]) => {
      const p = await priceFor(id, pos.outcome);
      const entry = pos.market_price ?? 0;
      const stake = pos.stake_usdc ?? pos.stake ?? 0;
      if (p != null && entry > 0) {
        const pct = p.mid / entry - 1;
        // Fair-value mark (mid), net of the sunk entry fee + exit fee at mid.
        const contracts = Math.max(1, Math.round(stake / entry));
        const fees = kalshiFee(entry, contracts) + kalshiFee(p.mid, contracts);
        marks[id] = {
          current_price: +p.mid.toFixed(3),
          sell_now_price: +p.sellNow.toFixed(3),
          ask_price: p.ask != null ? +p.ask.toFixed(3) : null,
          last_price: p.last != null ? +p.last.toFixed(3) : null,
          unrealized_pnl: +(stake * pct - fees).toFixed(2),
          unrealized_pct: +(pct * 100).toFixed(1),
          fees_usdc: +fees.toFixed(2),
        };
      } else {
        marks[id] = { current_price: null, unrealized_pnl: null, unrealized_pct: null };
      }
    })
  );

  cache = { at: Date.now(), data: marks };
  return NextResponse.json(marks);
}
