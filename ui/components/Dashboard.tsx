"use client";
import useSWR from "swr";
import TopBar from "./TopBar";
import SignalsTable from "./SignalsTable";
import Positions from "./Positions";
import PnLChart from "./PnLChart";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export default function Dashboard() {
  const { data: scan }     = useSWR("/api/scan",      fetcher, { refreshInterval: 5_000 });
  const { data: positions} = useSWR("/api/positions", fetcher, { refreshInterval: 5_000 });
  const { data: history }  = useSWR("/api/history",   fetcher, { refreshInterval: 5_000 });

  const signals  = scan?.signals   ?? [];
  const posMap   = positions        ?? {};
  const trades   = history?.trades  ?? [];
  const resolved = history?.resolved ?? [];

  return (
    <div className="flex flex-col h-screen overflow-hidden">
      <TopBar scan={scan} posCount={Object.keys(posMap).length} resolved={resolved} />

      <div className="flex flex-1 overflow-hidden gap-4 p-4">
        {/* Left: signals + history */}
        <div className="flex flex-col flex-1 overflow-hidden gap-4 min-w-0">
          <Tabs defaultValue="signals" className="flex flex-col flex-1 overflow-hidden">
            <TabsList className="bg-[#141824] border border-slate-800 w-fit">
              <TabsTrigger value="signals" className="data-[state=active]:bg-[#1e2535] data-[state=active]:text-white text-slate-400">
                Signals <span className="ml-2 bg-emerald-500/20 text-emerald-400 text-xs px-2 py-0.5 rounded-full">{signals.length}</span>
              </TabsTrigger>
              <TabsTrigger value="history" className="data-[state=active]:bg-[#1e2535] data-[state=active]:text-white text-slate-400">
                History <span className="ml-2 bg-slate-700 text-slate-300 text-xs px-2 py-0.5 rounded-full">{trades.length}</span>
              </TabsTrigger>
            </TabsList>

            <TabsContent value="signals" className="flex-1 overflow-hidden mt-0">
              <SignalsTable signals={signals} />
            </TabsContent>

            <TabsContent value="history" className="flex-1 overflow-auto mt-0">
              <PnLChart trades={trades} resolved={resolved} />
            </TabsContent>
          </Tabs>
        </div>

        {/* Right: positions */}
        <div className="w-80 flex-shrink-0 overflow-auto">
          <Positions posMap={posMap} signals={signals} />
        </div>
      </div>
    </div>
  );
}
