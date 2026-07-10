"use client";
import { useEffect, useState } from "react";
import { timeAgo } from "@/lib/format";

interface Props {
  scan: any;
  posCount: number;
}

export default function TopBar({ scan, posCount }: Props) {
  const mode = scan?.mode ?? "dry-run";
  const isLive = mode === "live";
  const activeMode = !!scan?.active_mode;
  const pollSec = scan?.poll_interval ?? null;
  const checked = scan?.markets_checked ?? 0;
  const scanDur = scan?.timings?.total_s ?? null;
  const maxOpen = scan?.max_open ?? 15;

  // re-render every second so the "Xs ago" chip ticks
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const ageS = scan?.last_scan
    ? (Date.now() - new Date(scan.last_scan).getTime()) / 1000
    : null;
  const stale = ageS != null && pollSec != null && ageS > pollSec * 3;

  const [busy, setBusy] = useState(false);
  const toggleActive = async () => {
    setBusy(true);
    try {
      await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active_mode: !activeMode }),
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <header className="flex items-center gap-3 px-5 h-14 border-b border-border bg-[#0d1119] shrink-0">
      <div className="flex items-center gap-2.5 mr-2">
        <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-[#3987e5] to-[#9085e9] flex items-center justify-center text-sm">
          ⚡
        </div>
        <div className="leading-tight">
          <div className="font-bold tracking-tight text-[15px]">PolyBot</div>
          <div className="text-[10px] text-muted-foreground -mt-0.5 tracking-wide uppercase">
            Kalshi Weather Desk
          </div>
        </div>
      </div>

      {/* Mode */}
      <span
        className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold tracking-wide ${
          isLive
            ? "bg-[#0ca30c]/15 text-[#2fc32f] ring-1 ring-[#0ca30c]/30"
            : "bg-[#3987e5]/15 text-[#6da7ec] ring-1 ring-[#3987e5]/30"
        }`}
      >
        <span
          className={`w-1.5 h-1.5 rounded-full animate-pulse ${
            isLive ? "bg-[#2fc32f]" : "bg-[#6da7ec]"
          }`}
        />
        {isLive ? "LIVE MONEY" : "DRY RUN"}
      </span>

      {/* Active-mode toggle */}
      <button
        onClick={toggleActive}
        disabled={busy}
        title={
          activeMode
            ? "Active trading on — click for standard mode"
            : "Standard mode — click to go active"
        }
        className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold transition-colors cursor-pointer ${
          activeMode
            ? "bg-[#c98500]/15 text-[#e5a940] ring-1 ring-[#c98500]/35 hover:bg-[#c98500]/25"
            : "bg-secondary text-muted-foreground ring-1 ring-border hover:bg-accent"
        } ${busy ? "opacity-50" : ""}`}
      >
        <span
          className={`w-1.5 h-1.5 rounded-full ${
            activeMode ? "bg-[#e5a940] animate-pulse" : "bg-muted-foreground"
          }`}
        />
        {activeMode ? "ACTIVE" : "STANDARD"}
        {activeMode && pollSec ? (
          <span className="font-medium opacity-70">· every {pollSec}s</span>
        ) : null}
      </button>

      {scan?.halted && (
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold bg-[#d03b3b]/15 text-[#e05252] ring-1 ring-[#d03b3b]/40">
          ⛔ CIRCUIT BREAKER — daily loss limit hit, no new entries today
        </span>
      )}

      <div className="flex-1" />

      {/* Scan status */}
      <div
        className={`flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg ring-1 ${
          stale
            ? "ring-[#d03b3b]/40 bg-[#d03b3b]/10 text-[#e05252]"
            : "ring-border bg-secondary/60 text-muted-foreground"
        }`}
      >
        <span
          className={`w-1.5 h-1.5 rounded-full ${
            stale ? "bg-[#e05252]" : "bg-[#2fc32f] animate-pulse"
          }`}
        />
        {stale ? (
          <span className="font-semibold">
            Bot stale — last scan {timeAgo(scan?.last_scan)}
          </span>
        ) : (
          <>
            <span>
              scanned <b className="text-foreground tnum">{checked}</b> markets
            </span>
            {scanDur != null && (
              <span>
                in <b className="text-foreground tnum">{scanDur}s</b>
              </span>
            )}
            <span className="tnum">{timeAgo(scan?.last_scan)}</span>
          </>
        )}
      </div>

      <div className="text-xs px-3 py-1.5 rounded-lg ring-1 ring-border bg-secondary/60 text-muted-foreground">
        positions{" "}
        <b className={`tnum ${posCount >= maxOpen ? "text-[#e5a940]" : "text-foreground"}`}>
          {posCount}/{maxOpen}
        </b>
      </div>
    </header>
  );
}
