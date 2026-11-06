"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Download, FileText, RefreshCw, Database } from "lucide-react";
import type { Document } from "@/lib/types";
import { CATEGORY_LABELS, CATEGORY_COLORS, formatBytes } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";

const STATUS_COLORS: Record<Document["status"], string> = {
  pending: "bg-yellow-100 text-yellow-800",
  processing: "bg-blue-100 text-blue-800 animate-pulse",
  indexed: "bg-green-100 text-green-800",
  error: "bg-red-100 text-red-800",
};

function SkeletonRow() {
  return (
    <tr className="bg-white">
      {[...Array(6)].map((_, i) => (
        <td key={i} className="px-4 py-3">
          <div className="h-4 bg-gray-200 rounded animate-pulse" style={{ width: `${60 + i * 10}%` }} />
        </td>
      ))}
    </tr>
  );
}

export function RecentDocuments() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const fetchDocs = async () => {
    try {
      setError(false);
      const res = await fetch("/api/ingest");
      if (!res.ok) throw new Error("Failed");
      const data = await res.json();
      // Most recent first, show up to 8
      const sorted = (data.documents ?? []).sort(
        (a: Document, b: Document) =>
          new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()
      );
      setDocuments(sorted.slice(0, 8));
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDocs();
    const iv = setInterval(fetchDocs, 15_000);
    return () => clearInterval(iv);
  }, []);

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
      <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
        <h2 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
          <Database className="h-4 w-4 text-gray-400" />
          Recently Indexed Files
        </h2>
        <button
          onClick={fetchDocs}
          disabled={loading}
          className="text-gray-400 hover:text-gray-600 transition-colors disabled:opacity-50"
          title="Refresh"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>

      {loading ? (
        <table className="w-full text-sm">
          <tbody className="divide-y divide-gray-100">
            {[...Array(4)].map((_, i) => <SkeletonRow key={i} />)}
          </tbody>
        </table>
      ) : error ? (
        <div className="py-10 text-center text-sm text-gray-500">
          <AlertIcon />
          <p className="mt-2">Could not load documents — check your database connection.</p>
        </div>
      ) : documents.length === 0 ? (
        <div className="py-12 text-center text-sm text-gray-500 space-y-2">
          <FileText className="h-8 w-8 text-gray-300 mx-auto" />
          <p>No documents indexed yet.</p>
          <p className="text-xs text-gray-400">
            Upload files on the{" "}
            <Link href="/admin/ingestion" className="text-blue-600 hover:underline">
              Documents page
            </Link>
            , or run{" "}
            <code className="bg-gray-100 px-1 rounded text-xs">
              POST /api/seed
            </code>{" "}
            to load sample data.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-100">
              <tr>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                  Document
                </th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                  Category
                </th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wide hidden sm:table-cell">
                  Part / Rev
                </th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wide hidden md:table-cell">
                  Size
                </th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                  Status
                </th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wide hidden sm:table-cell">
                  Indexed
                </th>
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {documents.map((doc) => (
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
                    {doc.pageCount && (
                      <span className="ml-6 text-xs text-gray-400">{doc.pageCount} pages</span>
                    )}
                  </td>
                  <td className="px-4 py-3 shrink-0">
                    <Badge className={CATEGORY_COLORS[doc.category]}>
                      {CATEGORY_LABELS[doc.category]}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 hidden sm:table-cell">
                    {doc.partNumber ? (
                      <div>
                        <span className="text-xs font-mono text-gray-700">{doc.partNumber}</span>
                        {doc.revision && (
                          <span className="ml-1.5 text-xs text-gray-400">Rev {doc.revision}</span>
                        )}
                      </div>
                    ) : (
                      <span className="text-xs text-gray-400">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-xs hidden md:table-cell">
                    {doc.fileSize ? formatBytes(doc.fileSize) : "—"}
                  </td>
                  <td className="px-4 py-3">
                    <Badge className={STATUS_COLORS[doc.status]}>{doc.status}</Badge>
                  </td>
                  <td className="px-4 py-3 text-gray-400 text-xs hidden sm:table-cell whitespace-nowrap">
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

function AlertIcon() {
  return (
    <svg className="h-8 w-8 text-gray-300 mx-auto" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
    </svg>
  );
}
