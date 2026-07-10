"use client";

const STYLES: Record<string, { label: string; cls: string }> = {
  "lock-harvest": {
    label: "lock harvest",
    cls: "bg-[#0ca30c]/15 text-[#2fc32f] ring-[#0ca30c]/30",
  },
  "favorite-hold": {
    label: "favorite",
    cls: "bg-[#3987e5]/15 text-[#6da7ec] ring-[#3987e5]/30",
  },
  "conviction-no": {
    label: "conviction NO",
    cls: "bg-[#9085e9]/15 text-[#a89ef0] ring-[#9085e9]/30",
  },
  "conviction-yes": {
    label: "conviction YES",
    cls: "bg-[#9085e9]/15 text-[#a89ef0] ring-[#9085e9]/30",
  },
  "momentum-scalp": {
    label: "momentum",
    cls: "bg-[#c98500]/15 text-[#e5a940] ring-[#c98500]/30",
  },
  scalp: {
    label: "scalp",
    cls: "bg-secondary text-muted-foreground ring-border",
  },
};

export default function StrategyBadge({ strategy }: { strategy?: string }) {
  const s = STYLES[strategy ?? ""] ?? STYLES.scalp;
  return (
    <span
      className={`inline-flex px-1.5 py-0.5 rounded text-[10px] font-semibold ring-1 whitespace-nowrap ${s.cls}`}
    >
      {s.label}
    </span>
  );
}
