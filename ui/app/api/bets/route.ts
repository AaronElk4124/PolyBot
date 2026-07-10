/**
 * GET /api/bets
 *
 * Returns every bet the bot has taken (dry-run or live), with full detail.
 * Use this in Postman to verify each trade manually.
 *
 * Optional query params:
 *   ?status=open      → only bets not yet resolved
 *   ?status=resolved  → only resolved bets (with win/loss outcome)
 *   ?city=chicago     → filter by city name (case-insensitive)
 */

import { NextRequest, NextResponse } from "next/server";
import { readFileSync, existsSync } from "fs";
import { join } from "path";

const DATA = join(process.cwd(), "data");

function parseJsonl(filename: string): any[] {
  const path = join(DATA, filename);
  if (!existsSync(path)) return [];
  return readFileSync(path, "utf-8")
    .trim()
    .split("\n")
    .filter(Boolean)
    .map((l) => JSON.parse(l));
}

function parseJson(filename: string): any {
  const path = join(DATA, filename);
  if (!existsSync(path)) return {};
  return JSON.parse(readFileSync(path, "utf-8"));
}

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const statusFilter = searchParams.get("status");  // "open" | "resolved" | null
  const cityFilter   = searchParams.get("city")?.toLowerCase();

  const trades        = parseJsonl("dry_run_log.jsonl");
  const resolved      = parseJsonl("resolved_bets.jsonl");
  const openPositions = parseJson("open_positions.json");

  // Build a lookup of resolved bets by market_id
  const resolvedByMarket: Record<string, any> = {};
  for (const r of resolved) {
    resolvedByMarket[r.market_id] = r;
  }

  const bets = trades.map((t) => {
    const resolution = resolvedByMarket[t.market_id] ?? null;
    const isOpen     = !resolution && !!openPositions[t.market_id];
    const cityMatch  = t.question?.match(/in (.+?) be /i)?.[1] ?? t.forecast_city ?? "";
    const bucket     = t.question?.toLowerCase().includes(" be ")
      ? t.question.slice(t.question.toLowerCase().indexOf(" be ") + 4)
          .replace(/\?.*/, "")
          .replace(/ on .+/, "")
          .trim()
      : "";

    return {
      // ── Identity ──────────────────────────────────────────────────
      market_id:    t.market_id,
      timestamp:    t.timestamp,
      city:         cityMatch,
      bucket,
      full_question: t.question,

      // ── Trade details ─────────────────────────────────────────────
      side:         t.outcome,          // "YES" or "NO"
      entry_price:  t.market_price,     // e.g. 0.22 = 22¢
      our_estimate: t.our_prob,         // model probability
      edge:         t.edge,             // our_prob - entry_price
      stake_usdc:   t.stake_usdc,

      // ── Forecast at time of bet ───────────────────────────────────
      forecast_high_f: t.forecast_high_f,
      forecast_low_f:  t.forecast_low_f,
      forecast_source: t.forecast_source,
      reason:          t.reason,

      // ── Resolution ───────────────────────────────────────────────
      status:           resolution ? "resolved" : isOpen ? "open" : "pending",
      resolved_outcome: resolution?.resolved_outcome ?? null,
      won:              resolution?.won ?? null,
      pnl_usdc:         resolution?.pnl_usdc ?? null,
      resolved_at:      resolution?.resolved_at ?? null,

      // ── Kalshi link ──────────────────────────────────────────────
      event_ticker:  t.market_id.split("-").slice(0, 2).join("-"),
      market_ticker: t.market_id,
    };
  });

  // Apply filters
  let result = bets;
  if (statusFilter) {
    result = result.filter((b) => b.status === statusFilter);
  }
  if (cityFilter) {
    result = result.filter((b) => b.city.toLowerCase().includes(cityFilter));
  }

  return NextResponse.json({
    count: result.length,
    filters: { status: statusFilter, city: cityFilter },
    bets: result,
  }, {
    headers: { "Access-Control-Allow-Origin": "*" },  // allow Postman
  });
}
