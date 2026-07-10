"use client";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

export default function PnLChart({ trades, resolved }: { trades: any[]; resolved: any[] }) {
  const wins   = resolved.filter((r) => r.won).length;
  const losses = resolved.length - wins;
  const pnl    = resolved.reduce((s, r) => s + (r.pnl_usdc ?? 0), 0);
  const wr     = resolved.length ? ((wins / resolved.length) * 100).toFixed(0) : null;

  const cumulativeData = resolved
    .sort((a, b) => new Date(a.resolved_at).getTime() - new Date(b.resolved_at).getTime())
    .reduce((acc: any[], r, i) => {
      const prev = acc[i - 1]?.cumPnl ?? 0;
      acc.push({ bet: i + 1, cumPnl: +(prev + r.pnl_usdc).toFixed(2), won: r.won });
      return acc;
    }, []);

  const lastPnl = cumulativeData[cumulativeData.length - 1]?.cumPnl ?? 0;
  const lineColor = lastPnl >= 0 ? "#34d399" : "#f87171";

  return (
    <div className="flex flex-col gap-4 p-1">
      {/* Summary cards */}
      <div className="grid grid-cols-4 gap-3">
        {[
          { label: "Total P&L", value: `$${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}`, color: pnl >= 0 ? "text-emerald-400" : "text-red-400" },
          { label: "Win Rate", value: wr ? `${wr}%` : "—", color: wr && +wr >= 50 ? "text-emerald-400" : "text-red-400" },
          { label: "Wins", value: wins.toString(), color: "text-emerald-400" },
          { label: "Losses", value: losses.toString(), color: "text-red-400" },
        ].map((s) => (
          <Card key={s.label} className="bg-[#141824] border-slate-800">
            <CardContent className="p-3">
              <div className="text-[10px] uppercase tracking-widest text-slate-500">{s.label}</div>
              <div className={`text-xl font-bold mt-0.5 ${s.color}`}>{s.value}</div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Chart */}
      {cumulativeData.length > 0 ? (
        <Card className="bg-[#141824] border-slate-800">
          <CardHeader className="pb-0 pt-4 px-5">
            <CardTitle className="text-sm text-slate-300">Cumulative P&L</CardTitle>
          </CardHeader>
          <CardContent className="p-5 pt-3">
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={cumulativeData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2535" />
                <XAxis dataKey="bet" tick={{ fill: "#64748b", fontSize: 11 }} label={{ value: "Bet #", position: "insideBottom", offset: -2, fill: "#475569", fontSize: 11 }} />
                <YAxis tick={{ fill: "#64748b", fontSize: 11 }} tickFormatter={(v) => `$${v}`} />
                <Tooltip
                  contentStyle={{ background: "#0d1117", border: "1px solid #1e2535", borderRadius: 8 }}
                  labelStyle={{ color: "#94a3b8" }}
                  formatter={(v) => [`$${Number(v ?? 0).toFixed(2)}`, "P&L"]}
                />
                <ReferenceLine y={0} stroke="#334155" strokeDasharray="4 4" />
                <Line type="monotone" dataKey="cumPnl" stroke={lineColor} strokeWidth={2}
                  dot={(p) => (
                    <circle key={p.key} cx={p.cx} cy={p.cy} r={4}
                      fill={p.payload.won ? "#34d399" : "#f87171"} stroke="none" />
                  )} />
              </LineChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      ) : (
        <div className="text-slate-600 text-sm text-center py-10 border border-slate-800 rounded-lg">
          No resolved bets yet — markets settle overnight
        </div>
      )}

      {/* Resolved bets table */}
      {resolved.length > 0 && (
        <Card className="bg-[#141824] border-slate-800">
          <CardHeader className="pb-0 pt-4 px-5">
            <CardTitle className="text-sm text-slate-300">Resolved Bets</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <div className="overflow-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-slate-800 text-slate-500">
                    {["Date", "Result", "What Happened", "Stake", "P&L", "Market"].map((h) => (
                      <th key={h} className="px-4 py-2 text-left font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[...resolved].reverse().map((r, i) => {
                    const city = r.question?.match(/in (.+?) be /i)?.[1] ?? "";
                    const bucket = r.question?.toLowerCase().includes(" be ")
                      ? r.question.slice(r.question.toLowerCase().indexOf(" be ") + 4).replace(/\?.*/, "").replace(/ on .+/, "").trim()
                      : r.question?.slice(0, 40);
                    return (
                      <tr key={i} className="border-b border-slate-800/50 hover:bg-[#1e2535]">
                        <td className="px-4 py-2 text-slate-400">
                          {new Date(r.resolved_at).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                        </td>
                        <td className="px-4 py-2">
                          <span className={r.won ? "text-emerald-400 font-bold" : "text-red-400 font-bold"}>
                            {r.won ? "✓ WIN" : "✗ LOSS"}
                          </span>
                        </td>
                        <td className="px-4 py-2 text-slate-400">
                          Bet {r.bet_outcome} ({city}) → {r.resolved_outcome} won
                        </td>
                        <td className="px-4 py-2 text-slate-300">${r.stake_usdc}</td>
                        <td className={`px-4 py-2 font-bold ${r.pnl_usdc >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                          ${r.pnl_usdc >= 0 ? "+" : ""}{r.pnl_usdc?.toFixed(2)}
                        </td>
                        <td className="px-4 py-2 text-slate-500 max-w-[200px] truncate">{bucket}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Logged trades */}
      {trades.length > 0 && (
        <Card className="bg-[#141824] border-slate-800">
          <CardHeader className="pb-0 pt-4 px-5">
            <CardTitle className="text-sm text-slate-300">Logged Trades ({trades.length})</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <div className="overflow-auto max-h-64">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-[#141824]">
                  <tr className="border-b border-slate-800 text-slate-500">
                    {["Time", "Side", "Stake", "Edge", "Market"].map((h) => (
                      <th key={h} className="px-4 py-2 text-left font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[...trades].reverse().map((t, i) => {
                    const bucket = t.question?.toLowerCase().includes(" be ")
                      ? t.question.slice(t.question.toLowerCase().indexOf(" be ") + 4).replace(/\?.*/, "").replace(/ on .+/, "").trim()
                      : t.question?.slice(0, 40);
                    return (
                      <tr key={i} className="border-b border-slate-800/50 hover:bg-[#1e2535]">
                        <td className="px-4 py-2 text-slate-500">
                          {new Date(t.timestamp).toLocaleDateString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                        </td>
                        <td className="px-4 py-2">
                          <Badge variant="outline" className={
                            t.outcome === "YES"
                              ? "border-emerald-500/40 text-emerald-400 bg-emerald-500/10"
                              : "border-red-500/40 text-red-400 bg-red-500/10"
                          }>
                            {t.outcome}
                          </Badge>
                        </td>
                        <td className="px-4 py-2 text-slate-300">${t.stake_usdc}</td>
                        <td className="px-4 py-2 text-emerald-400 font-bold">{((t.edge ?? 0) * 100).toFixed(1)}%</td>
                        <td className="px-4 py-2 text-slate-500 max-w-[200px] truncate">{bucket}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
