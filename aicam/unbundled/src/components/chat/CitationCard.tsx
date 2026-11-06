"use client";

import { useState } from "react";
import { Download, ExternalLink, X, FileText, ChevronDown } from "lucide-react";
import type { Citation } from "@/lib/types";
import { CATEGORY_LABELS, CATEGORY_COLORS } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";

interface CitationCardProps {
  citation: Citation;
}

export function CitationCard({ citation }: CitationCardProps) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-lg border border-gray-200 bg-white overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-gray-50 transition-colors"
      >
        <span className="flex-shrink-0 h-6 w-6 rounded-full bg-blue-600 text-white text-xs font-bold flex items-center justify-center">
          {citation.index}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <FileText className="h-4 w-4 text-gray-400 shrink-0" />
            <span className="text-sm font-medium text-gray-900 truncate">{citation.documentName}</span>
          </div>
          <div className="flex items-center gap-2 mt-0.5">
            <Badge className={CATEGORY_COLORS[citation.category]}>
              {CATEGORY_LABELS[citation.category]}
            </Badge>
            {citation.pageNumber && (
              <span className="text-xs text-gray-500">Page {citation.pageNumber}</span>
            )}
            {citation.sheetName && (
              <span className="text-xs text-gray-500">Sheet: {citation.sheetName}</span>
            )}
          </div>
        </div>
        <ChevronDown
          className={`h-4 w-4 text-gray-400 transition-transform ${expanded ? "rotate-180" : ""}`}
        />
      </button>

      {/* Expanded Content */}
      {expanded && (
        <div className="px-4 pb-4 border-t border-gray-100 space-y-3">
          <p className="text-sm text-gray-600 italic leading-relaxed mt-3">
            &ldquo;{citation.snippet}&rdquo;
          </p>

          <div className="flex items-center gap-2">
            <a
              href={citation.downloadUrl}
              download
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-blue-600 text-white text-xs font-medium hover:bg-blue-700 transition-colors"
            >
              <Download className="h-3.5 w-3.5" />
              Download File
            </a>
            <a
              href={citation.viewUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-gray-300 text-gray-700 text-xs font-medium hover:bg-gray-50 transition-colors"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              View Page
            </a>
          </div>
        </div>
      )}
    </div>
  );
}

// Inline citation reference (clickable number in message text)
interface InlineCitationProps {
  index: number;
  onHover?: (index: number | null) => void;
}

export function InlineCitation({ index, onHover }: InlineCitationProps) {
  return (
    <sup
      className="inline-flex items-center justify-center h-4 w-4 rounded-sm bg-blue-100 text-blue-700 text-[10px] font-bold cursor-pointer hover:bg-blue-200 transition-colors mx-0.5"
      onMouseEnter={() => onHover?.(index)}
      onMouseLeave={() => onHover?.(null)}
    >
      {index}
    </sup>
  );
}
