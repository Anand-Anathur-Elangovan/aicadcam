"use client";

// /studio — Split-screen CAPP workstation
// Layout: [Left: RoutingPanel 280px] [Center: Canvas3D/2D/FAI flex-1] [Right: GCodeMonacoEditor 380px]

import { useState, useCallback } from "react";
import dynamic from "next/dynamic";
import type { ProcessPlan } from "@/lib/providers/capp.provider";
import type { PartFeatures } from "@/lib/cad/featureExtractor";
import type { ParseResult } from "@/lib/gcode/parser";
import StudioHeader from "@/components/studio/StudioHeader";
import RoutingPanel from "@/components/studio/RoutingPanel";
import DiffViewerModal from "@/components/studio/DiffViewerModal";

// Dynamic imports to avoid SSR issues with Three.js + Monaco + pdfjs
const Canvas3D      = dynamic(() => import("@/components/studio/Canvas3D"),           { ssr: false });
const Canvas2D      = dynamic(() => import("@/components/studio/Canvas2D"),           { ssr: false });
const AS9102FormTable = dynamic(() => import("@/components/studio/AS9102FormTable"),  { ssr: false });
const GCodeMonacoEditor = dynamic(() => import("@/components/studio/GCodeMonacoEditor"), { ssr: false });

type ViewMode = "3d" | "2d" | "fai";

export default function StudioPage() {
  const [viewMode, setViewMode]       = useState<ViewMode>("3d");
  const [gcode, setGcode]             = useState<string>("");
  const [parseResult, setParseResult] = useState<ParseResult | null>(null);
  const [highlightLine, setHighlightLine] = useState<number | undefined>(undefined);
  const [isPlaying, setIsPlaying]     = useState(false);
  const [plan, setPlan]               = useState<ProcessPlan | null>(null);
  const [features, setFeatures]       = useState<PartFeatures | null>(null);
  const [pdfUrl, setPdfUrl]           = useState<string | undefined>(undefined);
  const [isGeneratingGCode, setIsGeneratingGCode] = useState(false);
  const [generatedGcodeSnapshot, setGeneratedGcodeSnapshot] = useState("");
  const [diffModalOpen, setDiffModalOpen] = useState(false);

  // When G-code changes (from editor or AI), re-parse it
  const handleGCodeChange = useCallback((value: string) => {
    setGcode(value);
    // Parse async to avoid blocking
    import("@/lib/gcode/parser").then(({ parseGCode }) => {
      try {
        const result = parseGCode(value);
        setParseResult(result);
      } catch {
        // ignore parse errors while typing
      }
    });
  }, []);

  // AI-generated G-code
  const handleGCodeGenerated = useCallback((newGCode: string) => {
    setIsGeneratingGCode(false);
    setGeneratedGcodeSnapshot(newGCode);  // save AI baseline for diff
    handleGCodeChange(newGCode);
    setViewMode("3d");
  }, [handleGCodeChange]);

  const handlePlanGenerated = useCallback((newPlan: ProcessPlan, newFeatures: PartFeatures) => {
    setPlan(newPlan);
    setFeatures(newFeatures);
  }, []);

  const handleFileUploaded = useCallback((url: string) => {
    setPdfUrl(url);
  }, []);

  // Load sample G-code for demo
  const loadSample = useCallback(async () => {
    const { SAMPLE_GCODE } = await import("@/lib/gcode/parser");
    handleGCodeChange(SAMPLE_GCODE);
  }, [handleGCodeChange]);

  return (
    <div className="flex flex-col h-screen bg-slate-950 text-slate-200 overflow-hidden">
      <StudioHeader
        viewMode={viewMode}
        onViewModeChange={setViewMode}
        partName={plan?.partName ?? features?.partName}
        isPlaying={isPlaying}
        onPlayToggle={() => setIsPlaying((p) => !p)}
      />

      <div className="flex flex-1 min-h-0">
        {/* ── Left Panel: Routing / CAPP ── */}
        <aside className="w-72 shrink-0 border-r border-slate-700 overflow-hidden flex flex-col">
          <RoutingPanel
            onPlanGenerated={handlePlanGenerated}
            onGCodeGenerated={(g) => { setIsGeneratingGCode(false); handleGCodeGenerated(g); }}
            onFileUploaded={handleFileUploaded}
          />
        </aside>

        {/* ── Center: Canvas (3D / 2D / FAI) ── */}
        <main className="flex-1 min-w-0 relative overflow-hidden">
          {viewMode === "3d" && (
            <Canvas3D
              parseResult={parseResult}
              highlightLine={highlightLine}
              isPlaying={isPlaying}
              onSegmentClick={(lineIdx) => {
                setHighlightLine(lineIdx);
                setIsPlaying(false);
              }}
            />
          )}

          {viewMode === "2d" && (
            <Canvas2D
              pdfUrl={pdfUrl}
              balloonsData={features?.tolerances}
            />
          )}

          {viewMode === "fai" && (
            <AS9102FormTable
              tolerances={features?.tolerances}
              partNumber={plan?.partNumber ?? features?.partNumber}
              partName={plan?.partName ?? features?.partName}
              revision={plan?.revision ?? features?.revision}
            />
          )}

          {/* Empty state with sample loader */}
          {viewMode === "3d" && !parseResult && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 pointer-events-none">
              <div className="text-slate-600 text-sm">No toolpath loaded</div>
              <button
                className="pointer-events-auto px-4 py-2 text-xs rounded bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 transition-colors"
                onClick={loadSample}
              >
                Load sample G-code
              </button>
            </div>
          )}
        </main>

        {/* ── Right Panel: Monaco G-Code Editor ── */}
        <aside className="w-96 shrink-0 border-l border-slate-700 overflow-hidden flex flex-col">
          <GCodeMonacoEditor
            value={gcode}
            onChange={handleGCodeChange}
            onLineClick={(lineIdx) => setHighlightLine(lineIdx)}
            highlightLine={highlightLine}
            readOnly={isGeneratingGCode}
            className="flex-1"
          />
          {/* Approve & Train button — appears after AI generates G-code */}
          {generatedGcodeSnapshot && (
            <div className="shrink-0 px-3 py-2 border-t border-slate-700 bg-slate-900">
              <button
                onClick={() => setDiffModalOpen(true)}
                className="w-full py-2 text-xs font-semibold rounded bg-green-700 hover:bg-green-600 text-white transition-colors"
              >
                ✓ Review Diff &amp; Approve
              </button>
            </div>
          )}
        </aside>
      </div>

      {/* Diff + Approval modal */}
      <DiffViewerModal
        open={diffModalOpen}
        onClose={() => setDiffModalOpen(false)}
        generatedGcode={generatedGcodeSnapshot}
        approvedGcode={gcode}
        plan={plan}
        features={features}
        onApproved={(diffId) => {
          console.log("[Studio] Diff approved, id:", diffId);
          setDiffModalOpen(false);
        }}
      />
    </div>
  );
}
