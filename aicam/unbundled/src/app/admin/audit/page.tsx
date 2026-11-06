import AuditTable from "@/components/admin/AuditTable";
import Link from "next/link";

export const metadata = { title: "ITAR Audit Trail — Manufacturing AI" };

export default function AuditPage() {
  return (
    <div className="min-h-screen bg-slate-950 text-slate-200">
      <header className="flex items-center gap-4 px-6 py-3 border-b border-slate-800 bg-slate-900">
        <Link href="/" className="text-slate-400 hover:text-slate-200 text-xs">← Home</Link>
        <span className="text-slate-600">|</span>
        <Link href="/admin/ingestion" className="text-slate-400 hover:text-slate-200 text-xs">Ingestion</Link>
        <span className="text-slate-600">|</span>
        <Link href="/admin/system" className="text-slate-400 hover:text-slate-200 text-xs">System</Link>
        <div className="flex-1" />
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse inline-block" />
          <span className="text-xs text-red-400 font-semibold">ITAR CONTROLLED</span>
        </div>
      </header>

      <div className="max-w-screen-xl mx-auto px-6 py-6 space-y-6">
        <div>
          <h1 className="text-xl font-bold text-slate-100">ITAR / AS9100 Audit Trail</h1>
          <p className="text-sm text-slate-400 mt-1">
            Immutable, tamper-evident log of all system actions. Every entry is SHA-256 checksummed.
            Logs are append-only and cannot be modified or deleted.
          </p>
        </div>

        {/* Legend */}
        <div className="flex flex-wrap gap-2 text-[10px]">
          {[
            { label: "Login/Logout",    color: "text-green-400 bg-green-950/40 border-green-800" },
            { label: "G-Code Approve",  color: "text-cyan-400 bg-cyan-950/40 border-cyan-800" },
            { label: "DNC Transfer",    color: "text-orange-400 bg-orange-950/40 border-orange-800" },
            { label: "Document",        color: "text-purple-400 bg-purple-950/40 border-purple-800" },
            { label: "FAI Sign-off",    color: "text-emerald-400 bg-emerald-950/40 border-emerald-800" },
            { label: "Model Fine-tune", color: "text-pink-400 bg-pink-950/40 border-pink-800" },
            { label: "System Change",   color: "text-yellow-400 bg-yellow-950/40 border-yellow-800" },
          ].map((l) => (
            <span key={l.label} className={`px-2 py-0.5 rounded border ${l.color}`}>{l.label}</span>
          ))}
        </div>

        <AuditTable />
      </div>
    </div>
  );
}
