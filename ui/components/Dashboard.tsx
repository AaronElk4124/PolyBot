"use client";
import useSWR from "swr";
import TopBar from "./TopBar";
import StatTiles from "./StatTiles";
import EquityChart from "./EquityChart";
import SignalsTable from "./SignalsTable";
import Positions from "./Positions";
import HistoryTable from "./HistoryTable";
import Analytics from "./Analytics";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export default function Dashboard() {
  const { data: scan } = useSWR("/api/scan", fetcher, { refreshInterval: 4_000 });
  const { data: positions } = useSWR("/api/positions", fetcher, { refreshInterval: 4_000 });
  const { data: history } = useSWR("/api/history", fetcher, { refreshInterval: 8_000 });
  const { data: marks } = useSWR("/api/marks", fetcher, { refreshInterval: 8_000 });

  const signals = scan?.signals ?? [];
  const posMap = positions ?? {};
  const resolved = history?.resolved ?? [];
  const posCount = Object.keys(posMap).length;

  const tabChip = (n: number, tone: "green" | "gray" = "gray") => (
    <span
      className={`ml-1.5 text-[10px] px-1.5 py-0.5 rounded-full tnum ${
        tone === "green"
          ? "bg-[#0ca30c]/20 text-[#2fc32f]"
          : "bg-secondary text-muted-foreground"
      }`}
    >
      {n}
    </span>
  );

  return (
    <div className="flex flex-col h-screen overflow-hidden">
      <TopBar scan={scan} posCount={posCount} />

      <main className="flex-1 overflow-auto p-4 flex flex-col gap-4">
        <StatTiles resolved={resolved} positions={posMap} marks={marks ?? {}} />

        <div className="grid xl:grid-cols-[1fr_320px] gap-4 flex-1 min-h-0 items-start">
          {/* Left column: equity + tabs */}
          <div className="flex flex-col gap-4 min-w-0">
            <div className="rounded-xl ring-1 ring-border bg-card p-4">
              <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1">
                Equity curve — cumulative realized P&L
              </div>
              <div className="h-56">
                <EquityChart resolved={resolved} />
              </div>
            </div>

            <Tabs defaultValue="signals" className="flex flex-col min-h-0">
              <TabsList className="bg-secondary/60 ring-1 ring-border w-fit">
                <TabsTrigger value="signals">
                  Signals{" "}
                  {tabChip(
                    signals.filter((s: any) => s.bettable).length,
                    signals.some((s: any) => s.bettable) ? "green" : "gray"
                  )}
                </TabsTrigger>
                <TabsTrigger value="history">
                  History {tabChip(resolved.length)}
                </TabsTrigger>
                <TabsTrigger value="analytics">Analytics</TabsTrigger>
              </TabsList>

              <TabsContent value="signals" className="mt-3 min-h-0">
                <div className="h-[540px]">
                  <SignalsTable signals={signals} heldIds={new Set(Object.keys(posMap))} />
                </div>
              </TabsContent>
              <TabsContent value="history" className="mt-3 min-h-0">
                <div className="h-[540px]">
                  <HistoryTable resolved={resolved} />
                </div>
              </TabsContent>
              <TabsContent value="analytics" className="mt-3 min-h-0">
                <Analytics resolved={resolved} positions={posMap} marks={marks ?? {}} />
              </TabsContent>
            </Tabs>
          </div>

          {/* Right rail: always-visible open positions */}
          <aside className="xl:sticky xl:top-0 flex flex-col gap-2 min-w-0">
            <div className="flex items-baseline justify-between px-1">
              <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                Open positions
              </h2>
              <span className="text-[11px] text-muted-foreground tnum">{posCount}</span>
            </div>
            <Positions posMap={posMap} marks={marks ?? {}} />
          </aside>
        </div>
      </main>
    </div>
  );
}
