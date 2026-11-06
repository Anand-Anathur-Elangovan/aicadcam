import TelemetryCards from "@/components/admin/TelemetryCards";
import Link from "next/link";

export const metadata = { title: "System Telemetry — Manufacturing AI" };

export default function SystemPage() {
  return (
    <div className="min-h-screen bg-slate-950 text-slate-200">
      <header className="flex items-center gap-4 px-6 py-3 border-b border-slate-800 bg-slate-900">
        <Link href="/" className="text-slate-400 hover:text-slate-200 text-xs">← Home</Link>
        <span className="text-slate-600">|</span>
        <Link href="/admin/ingestion" className="text-slate-400 hover:text-slate-200 text-xs">Ingestion</Link>
        <span className="text-slate-600">|</span>
        <Link href="/admin/audit" className="text-slate-400 hover:text-slate-200 text-xs">Audit Trail</Link>
      </header>

      <div className="max-w-screen-xl mx-auto px-6 py-6 space-y-6">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-xl font-bold text-slate-100">System Telemetry</h1>
            <p className="text-sm text-slate-400 mt-1">
              Live hardware metrics, GPU utilization, inference stats, and vector DB health.
              Refreshes every 10 seconds.
            </p>
          </div>
          <div className="text-xs text-slate-500 space-y-0.5 text-right">
            <div>Deployment: <span className="text-cyan-400">{process.env.DEPLOYMENT_MODE ?? "CLOUD"}</span></div>
            <div className="flex items-center gap-1 justify-end">
              <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse inline-block" />
              <span>Live</span>
            </div>
          </div>
        </div>

        <TelemetryCards />

        {/* DNC Machine Status */}
        <div className="bg-slate-800 border border-slate-700 rounded-xl p-4">
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-3">Shop Floor Machines (DNC)</h3>
          <MachineStatusTable />
        </div>

        {/* Quick links */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
          {[
            { label: "Audit Trail",    href: "/admin/audit",     color: "border-red-800 hover:bg-red-950/30" },
            { label: "CAPP Studio",    href: "/studio",          color: "border-cyan-800 hover:bg-cyan-950/30" },
            { label: "Document Ingest",href: "/admin/ingestion", color: "border-purple-800 hover:bg-purple-950/30" },
            { label: "AI Chat",        href: "/chat",            color: "border-blue-800 hover:bg-blue-950/30" },
          ].map((l) => (
            <Link key={l.label} href={l.href}
              className={`flex items-center justify-center py-2 rounded border ${l.color} text-slate-300 transition-colors`}>
              {l.label}
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Server component that loads machines from API ─────────────────────────────
async function MachineStatusTable() {
  try {
    const res = await fetch(
      `${process.env.NEXTAUTH_URL ?? "http://localhost:3000"}/api/dnc/transfer`,
      { cache: "no-store", headers: { authorization: `Bearer ${process.env.INTERNAL_API_TOKEN ?? ""}` } }
    );
    if (!res.ok) throw new Error("Not authorized");
    const data = await res.json() as { machines: Array<{ id: string; name: string; display_name: string; status: string; last_seen: string; protocol: string; host: string }> };
    if (!data.machines?.length) throw new Error("No machines");

    return (
      <table className="w-full text-[11px] border-collapse">
        <thead>
          <tr className="text-slate-400">
            {["Name", "Protocol", "Host", "Status", "Last Seen"].map((h) => (
              <th key={h} className="text-left pb-1 font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.machines.map((m) => (
            <tr key={m.id} className="border-t border-slate-700">
              <td className="py-1.5 text-slate-200">{m.display_name ?? m.name}</td>
              <td className="py-1.5 text-slate-400">{m.protocol}</td>
              <td className="py-1.5 font-mono text-slate-400">{m.host}</td>
              <td className="py-1.5">
                <span className={`px-1.5 py-0.5 rounded text-[10px] ${
                  m.status === "ONLINE"  ? "text-green-400 bg-green-950/40" :
                  m.status === "ERROR"   ? "text-red-400 bg-red-950/40" :
                  "text-slate-400 bg-slate-700"
                }`}>{m.status}</span>
              </td>
              <td className="py-1.5 text-slate-500">{m.last_seen ? new Date(m.last_seen).toLocaleString() : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  } catch {
    return <p className="text-xs text-slate-500">No machines registered. Add machines via the DNC API or admin panel.</p>;
  }
}
