/**
 * GET  /api/config   → current runtime toggle ({ active_mode })
 * POST /api/config   → set it, body { active_mode: boolean }
 *
 * The bot reads ui/data/runtime_config.json at the start of every scan,
 * so toggling here flips active mode live without restarting.
 */

import { NextRequest, NextResponse } from "next/server";
import { readFileSync, writeFileSync, existsSync } from "fs";
import { join } from "path";

const FILE = join(process.cwd(), "data", "runtime_config.json");

export async function GET() {
  if (!existsSync(FILE)) return NextResponse.json({ active_mode: null });
  try {
    return NextResponse.json(JSON.parse(readFileSync(FILE, "utf-8")));
  } catch {
    return NextResponse.json({ active_mode: null });
  }
}

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => ({}));
  const active_mode = !!body.active_mode;
  writeFileSync(FILE, JSON.stringify({ active_mode }, null, 2));
  return NextResponse.json({ active_mode });
}
