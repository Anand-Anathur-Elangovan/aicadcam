"use client";

import { useEffect, useState, useCallback } from "react";

interface AuditRow {
  id:            number;
  timestamp:     string;
  userEmail:     string | null;
  userRole:      string | null;
  ipAddress:     string | null;
  action:        string;
  resourceType:  string | null;
  resourceName:  string | null;
  details:       Record<string, unknown>;
  checksum:      string;
}

const ACTION_COLORS: Record<string, string> = {
  LOGIN:                 "text-green-400 bg-green-950/40",
  LOGOUT:                "text-slate-400 bg-slate-800",
  GCODE_APPROVE:         "text-cyan-400 bg-cyan-950/40",
  GCODE_GENERATE:        "text-blue-400 bg-blue-950/40",
  GCODE_EXPORT:          "text-violet-400 bg-violet-950/40",
  DNC_TRANSFER:          "text-orange-400 bg-orange-950/40",
  DOCUMENT_UPLOAD:       "text-purple-400 bg-purple-950/40",
  DOCUMENT_DOWNLOAD:     "text-purple-400 bg-purple-950/30",
  DOCUMENT_DELETE:       "text-red-400 bg-red-950/40",
  SYSTEM_SETTINGS_CHANGE: "text-yellow-400 bg-yellow-950/40",
  MODEL_FINETUNE_START:  "text-pink-400 bg-pink-950/40",
  FAI_SIGN:              "text-emerald-400 bg-emerald-950/40",
};

export default function AuditTable() {
  const [rows, setRows]         = useState<AuditRow[]>([]);
  const [total, setTotal]       = useState(0);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState<string | null>(null);
  const [search, setSearch]     = useState("");
  const [actionFilter, setActionFilter] = useState("");
  const [page, setPage]         = useState(0);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const limit = 50;

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        limit:  String(limit),
        offset: String(page * limit),
      });
      if (actionFilter) params.set("action", actionFilter);
      const res = await fetch(`/api/audit?${params}`);
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json() as { rows: AuditRow[]; total: number };
      setRows(data.rows);
      setTotal(data.total);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load audit logs");
    } finally {
      setLoading(false);
    }
  }, [page, actionFilter]);

  useEffect(() => { fetchLogs(); }, [fetchLogs]);

  const filtered = search
    ? rows.filter((r) =>
        r.userEmail?.includes(search) ||
        r.action.includes(search.toUpperCase()) ||
        r.resourceName?.includes(search) ||
        r.ipAddress?.includes(search)
      )
    : rows;

  const totalPages = Math.ceil(total / limit);

  const actions = [
    "", "LOGIN", "LOGOUT", "GCODE_APPROVE", "GCODE_GENERATE", "DNC_TRANSFER",
    "DOCUMENT_UPLOAD", "DOCUMENT_DOWNLOAD", "FAI_SIGN", "MODEL_FINETUNE_START",
  ];

  return (
    <div className="space-y-3">
      {/* Filters */}
      <div className="flex gap-2 flex-wrap">
        <input
          className="flex-1 min-w-48 text-xs bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-slate-200 focus:outline-none focus:border-cyan-500 placeholder:text-slate-500"
          placeholder="Search by email, IP, resource…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select
          className="text-xs bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-slate-200"
          value={actionFilter}
          onChange={(e) => { setActionFilter(e.target.value); setPage(0); }}
        >
          {actions.map((a) => <option key={a} value={a}>{a || "All Actions"}</option>)}
        </select>
        <button
          onClick={fetchLogs}
          className="px-3 py-1.5 text-xs rounded bg-slate-700 hover:bg-slate-600 text-slate-200"
        >
          Refresh
        </button>
        <span className="text-xs text-slate-500 self-center">{total.toLocaleString()} total entries</span>
      </div>

      {error && (
        <div className="text-xs text-red-400 bg-red-950/30 border border-red-800 rounded p-2">{error}</div>
      )}

      {/* Table */}
      <div className="overflow-auto rounded-lg border border-slate-700">
        <table className="w-full text-[11px] border-collapse">
          <thead className="bg-slate-800 sticky top-0">
            <tr>
              {["Timestamp", "User", "Role", "Action", "Resource", "IP Address", "Checksum"].map((h) => (
                <th key={h} className="text-left px-3 py-2 text-slate-400 font-medium border-b border-slate-700 whitespace-nowrap">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={7} className="text-center py-8 text-slate-500">Loading…</td></tr>
            ) : filtered.length === 0 ? (
              <tr><td colSpan={7} className="text-center py-8 text-slate-500">No audit entries found</td></tr>
            ) : (
              filtered.map((row) => (
                <>
                  <tr
                    key={row.id}
                    className="border-b border-slate-800 hover:bg-slate-800/50 cursor-pointer"
                    onClick={() => setExpandedId(expandedId === row.id ? null : row.id)}
                  >
                    <td className="px-3 py-1.5 whitespace-nowrap text-slate-400 font-mono">
                      {new Date(row.timestamp).toLocaleString()}
                    </td>
                    <td className="px-3 py-1.5 text-slate-200">{row.userEmail ?? "—"}</td>
                    <td className="px-3 py-1.5 text-slate-400">{row.userRole ?? "—"}</td>
                    <td className="px-3 py-1.5">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono ${ACTION_COLORS[row.action] ?? "text-slate-300 bg-slate-700"}`}>
                        {row.action}
                      </span>
                    </td>
                    <td className="px-3 py-1.5 text-slate-300 max-w-32 truncate" title={row.resourceName ?? ""}>
                      {row.resourceName ?? row.resourceType ?? "—"}
                    </td>
                    <td className="px-3 py-1.5 font-mono text-slate-500">{row.ipAddress ?? "—"}</td>
                    <td className="px-3 py-1.5 font-mono text-slate-600 text-[9px]">{row.checksum.slice(0, 12)}…</td>
                  </tr>

                  {/* Expanded details */}
                  {expandedId === row.id && (
                    <tr key={`${row.id}-details`} className="bg-slate-800/80 border-b border-slate-700">
                      <td colSpan={7} className="px-4 py-3">
                        <div className="text-[11px] text-slate-400 space-y-1">
                          <div><span className="text-slate-500">Full Checksum:</span> <span className="font-mono text-slate-300">{row.checksum}</span></div>
                          <div><span className="text-slate-500">Details:</span></div>
                          <pre className="text-[10px] bg-slate-900 rounded p-2 overflow-auto max-h-40 text-slate-300">
                            {JSON.stringify(row.details, null, 2)}
                          </pre>
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center gap-2 text-xs text-slate-400">
          <button
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
            className="px-3 py-1 rounded bg-slate-800 hover:bg-slate-700 disabled:opacity-40"
          >
            ‹ Prev
          </button>
          <span>Page {page + 1} of {totalPages}</span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
            disabled={page >= totalPages - 1}
            className="px-3 py-1 rounded bg-slate-800 hover:bg-slate-700 disabled:opacity-40"
          >
            Next ›
          </button>
        </div>
      )}
    </div>
  );
}
