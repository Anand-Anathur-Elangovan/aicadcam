"use client";

// AS9102 Form 3 — First Article Inspection (FAI) table
// Columns: Balloon | Characteristic | Nominal | Tolerance | Actual | Status
// Rows are pre-filled from extracted tolerances; actual can be manually entered.

import { useState } from "react";
import type { ToleranceSpec } from "@/lib/cad/featureExtractor";

export interface FAIRow {
  balloon: number;
  characteristic: string;
  nominal: string;
  tolerance: string;
  actual: string;
  status: "pass" | "fail" | "open";
  notes: string;
}

interface Props {
  tolerances?: ToleranceSpec[];
  partNumber?: string;
  partName?: string;
  revision?: string;
  className?: string;
}

function makeRows(tolerances: ToleranceSpec[]): FAIRow[] {
  return tolerances.map((t, i) => ({
    balloon:        i + 1,
    characteristic: t.feature,
    nominal:        String(t.nominal),
    tolerance:      t.tolerance,
    actual:         "",
    status:         "open",
    notes:          "",
  }));
}

const STATUS_COLORS: Record<FAIRow["status"], string> = {
  pass: "text-green-400",
  fail: "text-red-400",
  open: "text-yellow-400",
};

export default function AS9102FormTable({
  tolerances = [],
  partNumber = "—",
  partName = "—",
  revision = "A",
  className = "",
}: Props) {
  const [rows, setRows] = useState<FAIRow[]>(() => makeRows(tolerances));

  const updateRow = (idx: number, field: keyof FAIRow, value: string) => {
    setRows((prev) => prev.map((r, i) => {
      if (i !== idx) return r;
      const updated = { ...r, [field]: value };
      // Auto-compute status when actual is filled
      if (field === "actual" && value) {
        const numActual = parseFloat(value);
        const numNominal = parseFloat(r.nominal);
        const tolMatch = r.tolerance.match(/[±+\-]?\s*([\d.]+)/);
        if (!isNaN(numActual) && !isNaN(numNominal) && tolMatch) {
          const tol = parseFloat(tolMatch[1]);
          updated.status = Math.abs(numActual - numNominal) <= tol ? "pass" : "fail";
        }
      }
      return updated;
    }));
  };

  const exportCSV = () => {
    const header = "Balloon,Characteristic,Nominal,Tolerance,Actual,Status,Notes";
    const body = rows.map((r) =>
      [r.balloon, r.characteristic, r.nominal, r.tolerance, r.actual, r.status, r.notes]
        .map((v) => `"${v}"`)
        .join(",")
    ).join("\n");
    const blob = new Blob([header + "\n" + body], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `FAI_${partNumber}_${revision}.csv`;
    a.click();
  };

  const passCount = rows.filter((r) => r.status === "pass").length;
  const failCount = rows.filter((r) => r.status === "fail").length;
  const openCount = rows.filter((r) => r.status === "open").length;

  return (
    <div className={`flex flex-col h-full bg-slate-900 text-slate-200 text-xs ${className}`}>
      {/* Header */}
      <div className="flex items-start justify-between px-4 py-2 bg-slate-800 border-b border-slate-700 shrink-0">
        <div className="space-y-0.5">
          <div className="font-bold text-slate-100">AS9102 Form 3 — First Article Inspection</div>
          <div className="text-slate-400">
            Part: <span className="text-slate-200">{partName}</span> |
            P/N: <span className="text-slate-200">{partNumber}</span> |
            Rev: <span className="text-slate-200">{revision}</span>
          </div>
        </div>
        <div className="flex items-center gap-3 text-[11px]">
          <span className="text-green-400">✓ {passCount} Pass</span>
          <span className="text-red-400">✗ {failCount} Fail</span>
          <span className="text-yellow-400">○ {openCount} Open</span>
          <button
            onClick={exportCSV}
            className="ml-2 px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 text-slate-200 transition-colors"
          >
            Export CSV
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {rows.length === 0 ? (
          <div className="flex items-center justify-center h-full text-slate-500">
            No tolerance data extracted yet. Upload a drawing to populate the FAI table.
          </div>
        ) : (
          <table className="w-full border-collapse text-[11px]">
            <thead className="sticky top-0 bg-slate-800 z-10">
              <tr>
                {["#", "Characteristic", "Nominal", "Tolerance", "Actual", "Status", "Notes"].map((col) => (
                  <th key={col} className="text-left px-3 py-1.5 text-slate-400 font-medium border-b border-slate-700">
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, idx) => (
                <tr
                  key={row.balloon}
                  className={`border-b border-slate-800 hover:bg-slate-800/50 ${
                    row.status === "fail" ? "bg-red-950/20" : ""
                  }`}
                >
                  {/* Balloon */}
                  <td className="px-3 py-1.5">
                    <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-slate-700 text-slate-200 font-bold text-[10px]">
                      {row.balloon}
                    </span>
                  </td>

                  {/* Characteristic */}
                  <td className="px-3 py-1.5">
                    <input
                      className="bg-transparent w-full text-slate-200 focus:outline-none border-b border-transparent focus:border-slate-500"
                      value={row.characteristic}
                      onChange={(e) => updateRow(idx, "characteristic", e.target.value)}
                    />
                  </td>

                  {/* Nominal */}
                  <td className="px-3 py-1.5">
                    <input
                      className="bg-transparent w-20 text-slate-200 focus:outline-none border-b border-transparent focus:border-slate-500"
                      value={row.nominal}
                      onChange={(e) => updateRow(idx, "nominal", e.target.value)}
                    />
                  </td>

                  {/* Tolerance */}
                  <td className="px-3 py-1.5 text-violet-300 font-mono">
                    {row.tolerance}
                  </td>

                  {/* Actual (editable) */}
                  <td className="px-3 py-1.5">
                    <input
                      className="bg-slate-800 border border-slate-700 rounded px-1.5 py-0.5 w-20 focus:outline-none focus:border-cyan-500 text-slate-200"
                      placeholder="—"
                      value={row.actual}
                      onChange={(e) => updateRow(idx, "actual", e.target.value)}
                    />
                  </td>

                  {/* Status */}
                  <td className="px-3 py-1.5">
                    <select
                      className={`bg-transparent focus:outline-none ${STATUS_COLORS[row.status]} cursor-pointer`}
                      value={row.status}
                      onChange={(e) => updateRow(idx, "status", e.target.value as FAIRow["status"])}
                    >
                      <option value="open">Open</option>
                      <option value="pass">Pass</option>
                      <option value="fail">Fail</option>
                    </select>
                  </td>

                  {/* Notes */}
                  <td className="px-3 py-1.5">
                    <input
                      className="bg-transparent w-full text-slate-400 focus:outline-none border-b border-transparent focus:border-slate-500"
                      placeholder="add notes…"
                      value={row.notes}
                      onChange={(e) => updateRow(idx, "notes", e.target.value)}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
