import { NextResponse } from "next/server";
import { readFileSync, existsSync } from "fs";
import { join } from "path";

export async function GET() {
  const file = join(process.cwd(), "data", "open_positions.json");
  if (!existsSync(file)) return NextResponse.json({});
  return NextResponse.json(JSON.parse(readFileSync(file, "utf-8")));
}
