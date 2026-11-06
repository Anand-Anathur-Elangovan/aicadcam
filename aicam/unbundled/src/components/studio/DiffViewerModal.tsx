"use client";

// Split-screen modal: AI-generated G-code (left) vs Human-approved (right)
// Shows Myers diff highlighting: red=deleted, green=inserted, gray=unchanged
// Includes edit reason input and "Approve & Train" action.

import { useState, useCallback } from "react";
import type { ProcessPlan } from "@/lib/providers/capp.provider";
import type { PartFeatures } from "@/lib/cad/featureExtractor";

interface Props {
  open:           boolean;
  onClose:        () => void;
  generatedGcode: string;
  approvedGcode:  string;   // current editor value
  plan?:          ProcessPlan | null;
  features?:      PartFeatures | null;
  onApproved?:    (diffId: string) => void;
}

interface DiffStats {
  linesAdded:    number;
  linesRemoved:  number;
  linesUnchanged: number;
}

export default function DiffViewerModal({
  open, onClose, generatedGcode, approvedGcode, plan, features, onApproved,
}: Props) {
  const [editReason, setEditReason]   = useState("");
  const [rating, setRating]           = useState<number>(4);
  const [isApproving, setIsApproving] = useState(false);
  const [result, setResult]           = useState<{ diffId: string; stats: DiffStats } | null>(null);
  const [error, setError]             = useState<string | null>(null);

  const handleApprove = useCallback(async () => {
    setIsApproving(true);
    setError(null);

    try {
      const res = await fetch("/api/gcode/approve", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          generatedGcode,
          approvedGcode,
          editReason:    editReason || undefined,
          qualityRating: rating,
          features: features ? {
            partNumber:  features.partNumber,
            partName:    features.partName,
            revision:    features.revision,
            material:    features.material,
            partType:    features.partType,
            features:    features.features,
            tolerances:  features.tolerances,
          } : undefined,
          planJson: plan ?? undefined,
        }),
      });

      if (!res.ok) throw new Error(await res.text());
      const data = await res.json() as { diffId: string; stats: DiffStats };
      setResult(data);
      onApproved?.(data.diffId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Approval failed");
    } finally {
      setIsApproving(false);
    }
  }, [generatedGcode, approvedGcode, editReason, rating, plan, features, onApproved]);

  if (!open) return null;

  const genLines  = generatedGcode.split("\n");
  const appLines  = approvedGcode.split("\n");
  const maxLines  = Math.max(genLines.length, appLines.length);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="bg-slate-900 border border-slate-700 rounded-xl shadow-2xl w-full max-w-6xl max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700 shrink-0">
          <div>
            <h2 className="font-semibold text-slate-100">G-Code Diff — Approve & Train</h2>
            <p className="text-xs text-slate-400 mt-0.5">Review changes between AI output and your edits before committing to memory.</p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-200 text-xl">×</button>
        </div>

        {/* Success state */}
        {result ? (
          <div className="flex-1 flex flex-col items-center justify-center gap-4 p-8 text-center">
            <div className="text-4xl">✅</div>
            <h3 className="text-lg font-semibold text-slate-100">Approved & Saved to Memory</h3>
            <div className="flex gap-6 text-sm text-slate-400">
              <span className="text-green-400">+{result.stats.linesAdded} added</span>
              <span className="text-red-400">-{result.stats.linesRemoved} removed</span>
              <span className="text-slate-400">={result.stats.linesUnchanged} unchanged</span>
            </div>
            <p className="text-xs text-slate-500 max-w-sm">
              This correction has been embedded into vector memory and will be retrieved as a preference when similar parts are processed in the future.
            </p>
            <div className="flex gap-2">
              <code className="text-xs bg-slate-800 px-2 py-1 rounded text-slate-300">{result.diffId}</code>
              <button onClick={onClose} className="px-4 py-1.5 rounded bg-cyan-600 text-white text-sm hover:bg-cyan-500">Close</button>
            </div>
          </div>
        ) : (
          <>
            {/* Split diff view */}
            <div className="flex flex-1 min-h-0 gap-0">
              {/* Left: Generated */}
              <div className="flex-1 flex flex-col min-w-0 border-r border-slate-700">
                <div className="px-3 py-1.5 bg-slate-800 text-xs text-slate-400 shrink-0 flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-red-400 inline-block" />
                  AI Generated ({genLines.length} lines)
                </div>
                <div className="flex-1 overflow-auto font-mono text-[11px] text-slate-300">
                  {genLines.map((line, i) => {
                    const inApproved = appLines.includes(line);
                    return (
                      <div
                        key={i}
                        className={`flex ${!inApproved && line.trim() ? "bg-red-950/30" : ""}`}
                      >
                        <span className="w-10 text-right pr-2 text-slate-600 select-none shrink-0 leading-5">{i + 1}</span>
                        <span className={`flex-1 px-1 leading-5 whitespace-pre ${!inApproved && line.trim() ? "text-red-300" : ""}`}>{line}</span>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Right: Approved */}
              <div className="flex-1 flex flex-col min-w-0">
                <div className="px-3 py-1.5 bg-slate-800 text-xs text-slate-400 shrink-0 flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-green-400 inline-block" />
                  Engineer Approved ({appLines.length} lines)
                </div>
                <div className="flex-1 overflow-auto font-mono text-[11px] text-slate-300">
                  {appLines.map((line, i) => {
                    const inGenerated = genLines.includes(line);
                    return (
                      <div
                        key={i}
                        className={`flex ${!inGenerated && line.trim() ? "bg-green-950/30" : ""}`}
                      >
                        <span className="w-10 text-right pr-2 text-slate-600 select-none shrink-0 leading-5">{i + 1}</span>
                        <span className={`flex-1 px-1 leading-5 whitespace-pre ${!inGenerated && line.trim() ? "text-green-300" : ""}`}>{line}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>

            {/* Footer controls */}
            <div className="border-t border-slate-700 px-5 py-3 shrink-0 space-y-3">
              {error && (
                <div className="text-xs text-red-400 bg-red-950/40 border border-red-800 rounded px-3 py-2">{error}</div>
              )}

              <div className="flex items-start gap-4">
                {/* Edit reason */}
                <div className="flex-1">
                  <label className="text-xs text-slate-400 block mb-1">Edit Reason (optional — helps future AI learning)</label>
                  <input
                    className="w-full text-xs bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-slate-200 focus:outline-none focus:border-cyan-500 placeholder:text-slate-500"
                    placeholder="e.g. Reduced feed rate for titanium pocketing, changed G81 to G83 peck drilling"
                    value={editReason}
                    onChange={(e) => setEditReason(e.target.value)}
                  />
                </div>

                {/* Quality rating */}
                <div>
                  <label className="text-xs text-slate-400 block mb-1">AI Quality</label>
                  <div className="flex gap-1">
                    {[1, 2, 3, 4, 5].map((n) => (
                      <button
                        key={n}
                        onClick={() => setRating(n)}
                        className={`w-6 h-6 text-sm ${n <= rating ? "text-yellow-400" : "text-slate-600"}`}
                      >
                        ★
                      </button>
                    ))}
                  </div>
                </div>

                {/* Actions */}
                <div className="flex gap-2 items-end">
                  <button
                    onClick={onClose}
                    className="px-3 py-1.5 text-xs rounded border border-slate-600 text-slate-400 hover:text-slate-200"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleApprove}
                    disabled={isApproving}
                    className="px-4 py-1.5 text-xs font-semibold rounded bg-green-600 hover:bg-green-500 disabled:opacity-50 text-white transition-colors"
                  >
                    {isApproving ? "Saving…" : "✓ Approve & Train"}
                  </button>
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
