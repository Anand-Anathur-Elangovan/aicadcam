"use client";

import { useCallback, useState } from "react";
import { useDropzone } from "react-dropzone";
import {
  UploadCloud, X, FileText, File, CheckCircle, AlertCircle,
  ChevronDown, ChevronUp, CopyCheck, Trash2, Plus,
} from "lucide-react";
import { cn, formatBytes, DOCUMENT_CATEGORIES, CATEGORY_LABELS } from "@/lib/utils";
import type { DocumentCategory } from "@/lib/utils";
import type { DocumentMetaFields } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";

// ─── Types ────────────────────────────────────────────────────────────────────

interface PendingFile {
  file: File;
  category: DocumentCategory;
  meta: DocumentMetaFields;
  id: string;
  collapsed: boolean;           // per-file collapse to save space when many files
  batchApplied: boolean;        // visual indicator that batch meta was applied
}

interface UploadStatus {
  fileId: string;
  filename: string;
  progress: number;
  step: string;
  error?: string;
  documentId?: string;
  done: boolean;
}

const EMPTY_META: DocumentMetaFields = {
  docTitle: "", description: "", partName: "", partNumber: "",
  revision: "", developedDate: "", customerName: "", issueDetails: "",
};

// Which fields appear in the batch panel (the ones most likely to be shared)
const BATCH_FIELDS: (keyof DocumentMetaFields)[] = [
  "partName", "partNumber", "revision", "customerName", "developedDate",
];

// ─── Main Component ───────────────────────────────────────────────────────────

export function FileUploadZone() {
  const [pendingFiles, setPendingFiles] = useState<PendingFile[]>([]);
  const [uploads, setUploads]           = useState<UploadStatus[]>([]);
  const [isUploading, setIsUploading]   = useState(false);
  const [batchMeta, setBatchMeta]       = useState<DocumentMetaFields>({ ...EMPTY_META });
  const [batchApplyMode, setBatchApplyMode] = useState<"fill" | "overwrite">("fill");

  // ── Drop handler ──
  const onDrop = useCallback((accepted: File[]) => {
    const newFiles: PendingFile[] = accepted.map((file) => ({
      file,
      category: guessCategory(file.name) as DocumentCategory,
      meta: { ...EMPTY_META },
      id: crypto.randomUUID(),
      collapsed: false,
      batchApplied: false,
    }));
    setPendingFiles((prev) => [...prev, ...newFiles]);
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      "application/pdf": [".pdf"],
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
      "application/vnd.ms-excel": [".xls"],
      "text/csv": [".csv"],
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
      "image/png": [".png"],
      "image/jpeg": [".jpg", ".jpeg"],
    },
    maxSize: 100 * 1024 * 1024,
  });

  // ── Per-file helpers ──
  const updateCategory = (id: string, category: DocumentCategory) =>
    setPendingFiles((prev) => prev.map((f) => f.id === id ? { ...f, category } : f));

  const updateMeta = (id: string, key: keyof DocumentMetaFields, value: string) =>
    setPendingFiles((prev) =>
      prev.map((f) => f.id === id ? { ...f, meta: { ...f.meta, [key]: value } } : f)
    );

  const toggleCollapsed = (id: string) =>
    setPendingFiles((prev) => prev.map((f) => f.id === id ? { ...f, collapsed: !f.collapsed } : f));

  const removeFile = (id: string) =>
    setPendingFiles((prev) => prev.filter((f) => f.id !== id));

  const clearAll = () => setPendingFiles([]);

  // ── Collapse / expand all ──
  const setAllCollapsed = (collapsed: boolean) =>
    setPendingFiles((prev) => prev.map((f) => ({ ...f, collapsed })));

  // ── Apply batch metadata to all files ──
  const applyBatchToAll = () => {
    setPendingFiles((prev) =>
      prev.map((f) => {
        const merged = { ...f.meta };
        BATCH_FIELDS.forEach((key) => {
          const batchVal = batchMeta[key]?.trim();
          if (!batchVal) return;
          // "fill" = only set if file doesn't have its own value
          // "overwrite" = always overwrite
          if (batchApplyMode === "overwrite" || !merged[key]?.trim()) {
            (merged as Record<string, string>)[key] = batchVal;
          }
        });
        return { ...f, meta: merged, batchApplied: true };
      })
    );
  };

  // ── Apply batch metadata to a single file ──
  const applyBatchToOne = (id: string) => {
    setPendingFiles((prev) =>
      prev.map((f) => {
        if (f.id !== id) return f;
        const merged = { ...f.meta };
        BATCH_FIELDS.forEach((key) => {
          const batchVal = batchMeta[key]?.trim();
          if (batchVal) (merged as Record<string, string>)[key] = batchVal;
        });
        return { ...f, meta: merged, batchApplied: true };
      })
    );
  };

  // ── Upload all files ──
  const uploadAll = async () => {
    if (pendingFiles.length === 0 || isUploading) return;
    setIsUploading(true);

    setUploads(pendingFiles.map((pf) => ({
      fileId: pf.id, filename: pf.file.name, progress: 0, step: "Queued", done: false,
    })));

    for (const pf of pendingFiles) {
      const formData = new FormData();
      formData.append("file", pf.file);
      formData.append("category", pf.category);
      (Object.entries(pf.meta) as [keyof DocumentMetaFields, string][]).forEach(([key, val]) => {
        if (val?.trim()) formData.append(key, val.trim());
      });

      const response = await fetch("/api/ingest", { method: "POST", body: formData });
      if (!response.body) continue;

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const event = JSON.parse(line.slice(6)) as {
              step: string; progress: number; error?: string; documentId?: string;
            };
            setUploads((prev) =>
              prev.map((u) =>
                u.fileId === pf.id
                  ? { ...u, step: event.step, progress: event.progress,
                      error: event.error, documentId: event.documentId,
                      done: event.progress === 100 || !!event.error }
                  : u
              )
            );
          } catch { /* malformed SSE line */ }
        }
      }
    }

    setPendingFiles([]);
    setIsUploading(false);
  };

  const hasBatchData = BATCH_FIELDS.some((k) => batchMeta[k]?.trim());

  // ─────────────────────────────────────────────────────────────────────────────
  return (
    <div className="space-y-6">

      {/* ── Drop Zone ── */}
      <div
        {...getRootProps()}
        className={cn(
          "rounded-xl border-2 border-dashed p-10 text-center cursor-pointer transition-colors",
          isDragActive
            ? "border-blue-500 bg-blue-50"
            : "border-gray-300 bg-gray-50 hover:border-blue-400 hover:bg-blue-50/40"
        )}
      >
        <input {...getInputProps()} />
        <UploadCloud className="mx-auto h-12 w-12 text-gray-400" />
        <p className="mt-3 text-base font-medium text-gray-700">
          {isDragActive ? "Drop files here" : "Drag & drop files, or click to browse"}
        </p>
        <p className="mt-1 text-sm text-gray-500">
          PDF · XLSX · CSV · DOCX · PNG · JPG · Multiple files supported — up to 100 MB each
        </p>
      </div>

      {/* ── File Queue ── */}
      {pendingFiles.length > 0 && (
        <div className="space-y-4">

          {/* Queue header */}
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-center gap-3">
              <h3 className="font-semibold text-gray-900">
                {pendingFiles.length} file{pendingFiles.length > 1 ? "s" : ""} in queue
              </h3>
              {pendingFiles.length > 1 && (
                <div className="flex gap-1">
                  <button
                    onClick={() => setAllCollapsed(false)}
                    className="text-xs text-blue-600 hover:underline"
                  >
                    Expand all
                  </button>
                  <span className="text-gray-300">·</span>
                  <button
                    onClick={() => setAllCollapsed(true)}
                    className="text-xs text-blue-600 hover:underline"
                  >
                    Collapse all
                  </button>
                </div>
              )}
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={clearAll}
                className="flex items-center gap-1 text-xs text-gray-500 hover:text-red-500 transition-colors"
              >
                <Trash2 className="h-3.5 w-3.5" /> Clear all
              </button>
              <Button onClick={uploadAll} disabled={isUploading} className="gap-2">
                <UploadCloud className="h-4 w-4" />
                {isUploading ? "Uploading…" : `Upload & Index ${pendingFiles.length > 1 ? `All ${pendingFiles.length}` : ""}`}
              </Button>
            </div>
          </div>

          {/* ── BATCH METADATA PANEL (shown when 2+ files) ── */}
          {pendingFiles.length >= 2 && (
            <div className="rounded-xl border-2 border-blue-200 bg-blue-50 overflow-hidden">
              <div className="flex items-center gap-3 px-4 py-3 bg-blue-600">
                <CopyCheck className="h-5 w-5 text-white shrink-0" />
                <div className="flex-1">
                  <p className="text-sm font-semibold text-white">Batch Metadata</p>
                  <p className="text-xs text-blue-200">Fill once → apply to all files (great for same-product batches)</p>
                </div>
              </div>

              <div className="px-4 py-4 space-y-3">
                {/* Batch fields grid */}
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  <Field
                    label="Part Name"
                    placeholder="e.g. Flange Body"
                    value={batchMeta.partName ?? ""}
                    onChange={(v) => setBatchMeta((b) => ({ ...b, partName: v }))}
                  />
                  <Field
                    label="Part Number"
                    placeholder="e.g. DWG-A901"
                    value={batchMeta.partNumber ?? ""}
                    onChange={(v) => setBatchMeta((b) => ({ ...b, partNumber: v }))}
                    monospace
                  />
                  <Field
                    label="Revision"
                    placeholder="e.g. REV3, B"
                    value={batchMeta.revision ?? ""}
                    onChange={(v) => setBatchMeta((b) => ({ ...b, revision: v }))}
                    monospace
                  />
                  <Field
                    label="Customer Name"
                    placeholder="e.g. Tata Motors"
                    value={batchMeta.customerName ?? ""}
                    onChange={(v) => setBatchMeta((b) => ({ ...b, customerName: v }))}
                  />
                  <Field
                    label="Developed Date"
                    type="date"
                    value={batchMeta.developedDate ?? ""}
                    onChange={(v) => setBatchMeta((b) => ({ ...b, developedDate: v }))}
                  />
                </div>

                {/* Apply controls */}
                <div className="flex items-center gap-3 flex-wrap pt-1">
                  <Button
                    onClick={applyBatchToAll}
                    disabled={!hasBatchData}
                    className="gap-2 bg-blue-600 hover:bg-blue-700 text-white"
                    size="sm"
                  >
                    <CopyCheck className="h-4 w-4" />
                    Apply to All {pendingFiles.length} Files
                  </Button>

                  {/* Fill vs Overwrite toggle */}
                  <div className="flex items-center gap-2 text-xs text-blue-700">
                    <label className="flex items-center gap-1.5 cursor-pointer">
                      <input
                        type="radio"
                        name="batchMode"
                        checked={batchApplyMode === "fill"}
                        onChange={() => setBatchApplyMode("fill")}
                        className="accent-blue-600"
                      />
                      Fill empty only
                    </label>
                    <label className="flex items-center gap-1.5 cursor-pointer">
                      <input
                        type="radio"
                        name="batchMode"
                        checked={batchApplyMode === "overwrite"}
                        onChange={() => setBatchApplyMode("overwrite")}
                        className="accent-blue-600"
                      />
                      Overwrite all
                    </label>
                  </div>

                  {!hasBatchData && (
                    <span className="text-xs text-blue-500 italic">Fill at least one field above to enable</span>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* ── Individual File Cards ── */}
          {pendingFiles.map((pf, idx) => (
            <div key={pf.id} className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">

              {/* File header bar */}
              <div className="flex items-center gap-3 px-4 py-3 bg-gray-50 border-b border-gray-200">
                <span className="flex-shrink-0 h-6 w-6 rounded-full bg-gray-200 text-gray-600 text-xs font-bold flex items-center justify-center">
                  {idx + 1}
                </span>
                <FileIcon mimeType={pf.file.type} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="truncate text-sm font-semibold text-gray-900">{pf.file.name}</p>
                    {pf.batchApplied && (
                      <span className="shrink-0 inline-flex items-center gap-1 rounded-full bg-blue-100 px-2 py-0.5 text-[10px] font-medium text-blue-700">
                        <CopyCheck className="h-3 w-3" /> Batch applied
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-gray-500">{formatBytes(pf.file.size)}</p>
                </div>

                {/* Category */}
                <select
                  value={pf.category}
                  onChange={(e) => updateCategory(pf.id, e.target.value as DocumentCategory)}
                  className="text-xs border border-gray-300 rounded-md px-2 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  {DOCUMENT_CATEGORIES.map((c) => (
                    <option key={c} value={c}>{CATEGORY_LABELS[c]}</option>
                  ))}
                </select>

                {/* Apply batch to this file only */}
                {pendingFiles.length >= 2 && hasBatchData && (
                  <button
                    onClick={() => applyBatchToOne(pf.id)}
                    title="Apply batch metadata to this file only"
                    className="text-xs text-blue-600 hover:text-blue-800 font-medium shrink-0"
                  >
                    Apply batch
                  </button>
                )}

                {/* Collapse toggle */}
                <button
                  onClick={() => toggleCollapsed(pf.id)}
                  className="text-gray-400 hover:text-gray-600 transition-colors"
                  title={pf.collapsed ? "Expand details" : "Collapse details"}
                >
                  {pf.collapsed ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
                </button>

                {/* Remove */}
                <button onClick={() => removeFile(pf.id)} className="text-gray-400 hover:text-red-500 transition-colors">
                  <X className="h-4 w-4" />
                </button>
              </div>

              {/* Collapsed summary */}
              {pf.collapsed ? (
                <div className="px-4 py-2 flex flex-wrap gap-1.5">
                  {hasAnyMeta(pf.meta) ? (
                    <>
                      {pf.meta.partNumber   && <Badge className="bg-blue-100 text-blue-800 font-mono text-[11px]">P/N: {pf.meta.partNumber}</Badge>}
                      {pf.meta.revision     && <Badge className="bg-purple-100 text-purple-800 font-mono text-[11px]">Rev: {pf.meta.revision}</Badge>}
                      {pf.meta.customerName && <Badge className="bg-orange-100 text-orange-800 text-[11px]">{pf.meta.customerName}</Badge>}
                      {pf.meta.developedDate && <Badge className="bg-green-100 text-green-800 text-[11px]">{pf.meta.developedDate}</Badge>}
                      {pf.meta.docTitle     && <Badge className="bg-gray-100 text-gray-700 text-[11px]">{pf.meta.docTitle}</Badge>}
                    </>
                  ) : (
                    <span className="text-xs text-gray-400 italic">No metadata — click ↑ to add details</span>
                  )}
                </div>
              ) : (
                /* Expanded metadata form */
                <div className="px-4 py-4 space-y-4">
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
                    Document Details <span className="font-normal normal-case text-gray-400">— all optional</span>
                  </p>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <Field label="Title" placeholder="e.g. Flange Assembly Drawing"
                      value={pf.meta.docTitle ?? ""} onChange={(v) => updateMeta(pf.id, "docTitle", v)} />
                    <Field label="Part Name" placeholder="e.g. Flange Body, Shaft Cover"
                      value={pf.meta.partName ?? ""} onChange={(v) => updateMeta(pf.id, "partName", v)} />
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <Field label="Part Number" placeholder="e.g. DWG-A901" monospace
                      value={pf.meta.partNumber ?? ""} onChange={(v) => updateMeta(pf.id, "partNumber", v)} />
                    <Field label="Revision" placeholder="e.g. REV3, B, 02" monospace
                      value={pf.meta.revision ?? ""} onChange={(v) => updateMeta(pf.id, "revision", v)} />
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <Field label="Customer Name" placeholder="e.g. Tata Motors, Bosch"
                      value={pf.meta.customerName ?? ""} onChange={(v) => updateMeta(pf.id, "customerName", v)} />
                    <Field label="Developed / Drawn Date" type="date"
                      value={pf.meta.developedDate ?? ""} onChange={(v) => updateMeta(pf.id, "developedDate", v)} />
                  </div>

                  <Field label="Description" placeholder="Brief summary of what this document covers…" multiline
                    value={pf.meta.description ?? ""} onChange={(v) => updateMeta(pf.id, "description", v)} />

                  <Field label="Issue Details / NCR / Deviation"
                    placeholder="Known issues, non-conformance reports, ECOs, deviations…" multiline
                    value={pf.meta.issueDetails ?? ""} onChange={(v) => updateMeta(pf.id, "issueDetails", v)} />

                  {hasAnyMeta(pf.meta) && (
                    <div className="flex flex-wrap gap-1.5 pt-1 border-t border-gray-100">
                      {pf.meta.partNumber   && <Badge className="bg-blue-100 text-blue-800 font-mono">P/N: {pf.meta.partNumber}</Badge>}
                      {pf.meta.revision     && <Badge className="bg-purple-100 text-purple-800 font-mono">Rev: {pf.meta.revision}</Badge>}
                      {pf.meta.customerName && <Badge className="bg-orange-100 text-orange-800">Customer: {pf.meta.customerName}</Badge>}
                      {pf.meta.developedDate && <Badge className="bg-green-100 text-green-800">Date: {pf.meta.developedDate}</Badge>}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}

          {/* Add more files button at bottom of queue */}
          <div
            {...getRootProps()}
            className="rounded-xl border-2 border-dashed border-gray-200 py-4 text-center cursor-pointer hover:border-blue-400 hover:bg-blue-50/30 transition-colors"
          >
            <input {...getInputProps()} />
            <div className="flex items-center justify-center gap-2 text-sm text-gray-500">
              <Plus className="h-4 w-4" />
              Add more files to this batch
            </div>
          </div>
        </div>
      )}

      {/* ── Upload Progress ── */}
      {uploads.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold text-gray-900">Indexing Progress</h3>
            <span className="text-xs text-gray-500">
              {uploads.filter((u) => u.done && !u.error).length}/{uploads.length} complete
            </span>
          </div>
          {uploads.map((u) => (
            <div key={u.fileId} className="rounded-lg border border-gray-200 bg-white p-4 space-y-2">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  {u.done && !u.error
                    ? <CheckCircle className="h-4 w-4 text-green-500 shrink-0" />
                    : u.error
                    ? <AlertCircle className="h-4 w-4 text-red-500 shrink-0" />
                    : <div className="h-4 w-4 rounded-full border-2 border-blue-500 border-t-transparent animate-spin shrink-0" />
                  }
                  <p className="text-sm font-medium text-gray-900 truncate">{u.filename}</p>
                </div>
                <span className="text-xs font-semibold text-gray-500 shrink-0">{u.progress}%</span>
              </div>
              <Progress value={u.progress} />
              <p className="text-xs text-gray-500">
                {u.error ?? u.step}
                {u.documentId && <span className="text-gray-400"> · ID: {u.documentId.slice(0, 8)}…</span>}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function Field({
  label, placeholder = "", value, onChange, type = "text", multiline = false, monospace = false,
}: {
  label: string; placeholder?: string; value: string;
  onChange: (v: string) => void; type?: string; multiline?: boolean; monospace?: boolean;
}) {
  const cls = cn(
    "w-full rounded-lg border border-gray-300 px-3 py-2 text-sm bg-white",
    "focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent",
    "placeholder:text-gray-400 transition-colors",
    monospace && "font-mono tracking-tight"
  );
  return (
    <div>
      <label className="block text-xs font-medium text-gray-600 mb-1">{label}</label>
      {multiline
        ? <textarea rows={2} placeholder={placeholder} value={value}
            onChange={(e) => onChange(e.target.value)} className={cn(cls, "resize-none")} />
        : <input type={type} placeholder={placeholder} value={value}
            onChange={(e) => onChange(e.target.value)} className={cls} />
      }
    </div>
  );
}

function FileIcon({ mimeType }: { mimeType: string }) {
  return mimeType === "application/pdf"
    ? <FileText className="h-8 w-8 text-red-500 shrink-0" />
    : <File className="h-8 w-8 text-blue-500 shrink-0" />;
}

function hasAnyMeta(meta: DocumentMetaFields) {
  return Object.values(meta).some((v) => v?.trim());
}

function guessCategory(filename: string): DocumentCategory {
  const l = filename.toLowerCase();
  if (l.includes("dwg") || l.includes("drawing"))                          return "DRAWING_2D";
  if (l.includes("fir") || l.includes("inspection"))                       return "INSPECTION_FIR";
  if (l.includes("po")  || l.includes("invoice"))                          return "FINANCIAL_PO_INVOICE";
  if (l.includes("store") || l.includes("inventory") || l.includes("fg")) return "STORES_FG";
  if (l.includes("tool") || l.includes("material") || l.includes("mtc"))  return "TOOLING_MATERIAL";
  return "GENERAL";
}
