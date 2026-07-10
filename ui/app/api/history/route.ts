import { NextResponse } from "next/server";
import { readFileSync, existsSync } from "fs";
import { join } from "path";

export async function GET() {
  const dir = join(process.cwd(), "data");

  const parse = (name: string) => {
    const path = join(dir, name);
    return existsSync(path)
      ? readFileSync(path, "utf-8").trim().split("\n").filter(Boolean).map(l => JSON.parse(l))
      : [];
  };

  return NextResponse.json({
    trades:   parse("dry_run_log.jsonl"),
    resolved: parse("resolved_bets.jsonl"),
  });
}
