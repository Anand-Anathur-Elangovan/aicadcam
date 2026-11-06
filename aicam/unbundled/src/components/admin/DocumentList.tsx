"use client";

import { useEffect, useState, useMemo } from "react";
import { Download, RefreshCw, FileText, Search, X } from "lucide-react";
import type { Document } from "@/lib/types";
import { CATEGORY_LABELS, CATEGORY_COLORS, DOCUMENT_CATEGORIES, formatBytes } from "@/lib/utils";
import type { DocumentCategory } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const STATUS_FILTERS = ["all", "indexed", "processing", "error"] as const;

const STATUS_COLORS: Record<Document["status"], string> = {
  pending: "bg-yellow-100 text-yellow-800",
  processing: "bg-blue-100 text-blue-800",
  indexed: "bg-green-100 text-green-800",
  error: "bg-red-100 text-red-800",
};

function SkeletonRow() {
  return (
    <tr>
      {[...Array(7)].map((_, i) => (
        <td key={i} className="px-4 py-3">
          <div className="h-4 bg-gray-200 rounded animate-pulse" style={{ width: `${50 + (i * 7) % 40}%` }} />
        </td>
      ))}
    </tr>
  );
}

export function DocumentList() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [categoryFilter, setCategoryFilter] = useState<string>("all");
  const [search, setSearch] = useState("");

  const fetchDocuments = async () => {
    setLoading(true);
    try {
      const params = statusFilter !== "all" ? `?status=${statusFilter}` : "";
      const res = await fetch(`/api/ingest${params}`);
      const data = await res.json();
      setDocuments(data.documents ?? []);
    } catch {
      setDocuments([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDocuments();
    const interval = setInterval(fetchDocuments, 10_000);
    return () => clearInterval(interval);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter]);

  // Client-side filter by category + search term
  const filtered = useMemo(() => {
    let list = documents;
    if (categoryFilter !== "all") {
      list = list.filter((d) => d.category === categoryFilter);
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(
        (d) =>
          d.originalName.toLowerCase().includes(q) ||
          (d.partNumber ?? "").toLowerCase().includes(q) ||
          (d.docTitle ?? "").toLowerCase().includes(q) ||
          (d.customerName ?? "").toLowerCase().includes(q)
      );
    }
    return list;
  }, [documents, categoryFilter, search]);

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex flex-col sm:flex-row gap-3 items-start sm:items-center justify-between">
        {/* Search */}
        <div className="relative w-full sm:w-64">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-gray-400 pointer-events-none" />
          <input
            type="text"
            placeholder="Search name, part number…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-8 pr-8 py-1.5 text-sm border border-gray-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
          {search && (
            <button
              onClick={() => setSearch("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>

        {/* Filters + Refresh */}
        <div className="flex flex-wrap items-center gap-2">
          {/* Status filter */}
          <div className="flex gap-1">
            {STATUS_FILTERS.map((s) => (
              <button
                key={s}
                onClick={() => setStatusFilter(s)}
                className={`px-2.5 py-1 rounded-full text-xs font-medium capitalize transition-colors ${
                  statusFilter === s
                    ? "bg-blue-600 text-white"
                    : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                }`}
              >
                {s}
              </button>
            ))}
          </div>

          {/* Category filter */}
          <select
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
            className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="all">All Categories</option>
            {DOCUMENT_CATEGORIES.map((cat) => (
              <option key={cat} value={cat}>
                {CATEGORY_LABELS[cat as DocumentCategory]}
              </option>
            ))}
          </select>

          <Button variant="ghost" size="sm" onClick={fetchDocuments} disabled={loading}>
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </Button>
        </div>
      </div>

      {/* Results count */}
      {!loading && (
        <p className="text-xs text-gray-500">
          {filtered.length} document{filtered.length !== 1 ? "s" : ""}
          {(search || categoryFilter !== "all" || statusFilter !== "all") &&
            ` (filtered from ${documents.length} total)`}
        </p>
      )}

      {/* Table */}
      {loading && documents.length === 0 ? (
        <div className="rounded-lg border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <tbody className="divide-y divide-gray-100 bg-white">
              {[...Array(5)].map((_, i) => <SkeletonRow key={i} />)}
            </tbody>
          </table>
        </div>
      ) : filtered.length === 0 ? (
        <div className="py-16 text-center text-sm text-gray-500 rounded-lg border border-gray-200 bg-white">
          <FileText className="h-8 w-8 text-gray-300 mx-auto mb-2" />
          {documents.length === 0
            ? "No documents found. Upload files to get started."
            : "No documents match your filters."}
        </div>
      ) : (
        <div className="rounded-lg border border-gray-200 overflow-hidden overflow-x-auto">
          <table className="w-full text-sm min-w-[700px]">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="px-4 py-3 text-left font-medium text-gray-600 text-xs uppercase tracking-wide">Document</th>
                <th className="px-4 py-3 text-left font-medium text-gray-600 text-xs uppercase tracking-wide">Category</th>
                <th className="px-4 py-3 text-left font-medium text-gray-600 text-xs uppercase tracking-wide">Part / Rev</th>
                <th className="px-4 py-3 text-left font-medium text-gray-600 text-xs uppercase tracking-wide">Size</th>
                <th className="px-4 py-3 text-left font-medium text-gray-600 text-xs uppercase tracking-wide">Status</th>
                <th className="px-4 py-3 text-left font-medium text-gray-600 text-xs uppercase tracking-wide">Indexed</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {filtered.map((doc) => (
                <tr key={doc.id} className="bg-white hover:bg-gray-50 transition-colors">
                  <td className="px-4 py-3 max-w-xs">
                    <div className="flex items-center gap-2">
                      <FileText className="h-4 w-4 text-gray-400 shrink-0" />
                      <span
                        className="font-medium text-gray-900 truncate"
                        title={doc.originalName}
                      >
                        {doc.originalName}
                      </span>
                    </div>
                    <div className="ml-6 flex items-center gap-2 mt-0.5">
                      {doc.pageCount && (
                        <span className="text-xs text-gray-400">{doc.pageCount} pages</span>
                      )}
                      {doc.customerName && (
                        <span className="text-xs text-gray-400 truncate max-w-[160px]" title={doc.customerName}>
                          {doc.customerName}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 shrink-0">
                    <Badge className={CATEGORY_COLORS[doc.category]}>
                      {CATEGORY_LABELS[doc.category]}
                    </Badge>
                  </td>
                  <td className="px-4 py-3">
                    {doc.partNumber ? (
                      <div>
                        <span className="text-xs font-mono font-semibold text-gray-800">
                          {doc.partNumber}
                        </span>
                        {doc.revision && (
                          <span className="ml-1.5 text-xs text-gray-400">Rev {doc.revision}</span>
                        )}
                      </div>
                    ) : (
                      <span className="text-xs text-gray-400">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-xs">
                    {doc.fileSize ? formatBytes(doc.fileSize) : "—"}
                  </td>
                  <td className="px-4 py-3">
                    <Badge className={STATUS_COLORS[doc.status]}>{doc.status}</Badge>
                    {doc.errorMessage && (
                      <p
                        className="mt-1 text-xs text-red-600 max-w-[200px] truncate"
                        title={doc.errorMessage}
                      >
                        {doc.errorMessage}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap">
                    {new Date(doc.createdAt).toLocaleDateString()}
                  </td>
                  <td className="px-4 py-3">
                    <a
                      href={`/api/files/download/${doc.id}`}
                      className="inline-flex items-center gap-1 text-blue-600 hover:text-blue-800 text-xs font-medium whitespace-nowrap"
                    >
                      <Download className="h-3 w-3" />
                      Download
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
