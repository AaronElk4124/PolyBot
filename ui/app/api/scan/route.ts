import { NextResponse } from "next/server";
import { readFileSync, existsSync } from "fs";
import { join } from "path";

export async function GET() {
  const file = join(process.cwd(), "data", "scan_state.json");
  if (!existsSync(file)) return NextResponse.json({ signals: [], markets_checked: 0 });
  return NextResponse.json(JSON.parse(readFileSync(file, "utf-8")));
}
