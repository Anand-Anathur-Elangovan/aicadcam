"use client";

// Canvas2D — PDF viewer with AS9102 balloon overlay
// Uses native <canvas> to render a PDF page and draw inspection balloons on top.
// Depends on pdfjs-dist (CDN loaded lazily to avoid SSR issues).

import { useEffect, useRef, useState, useCallback } from "react";
import type { ToleranceSpec } from "@/lib/cad/featureExtractor";

interface Balloon {
  id: number;
  x: number;         // percentage of canvas width
  y: number;         // percentage of canvas height
  label: string;
  tolerance?: string;
  diameter?: string;
}

interface Props {
  pdfUrl?: string;         // object URL or API URL of the PDF
  balloonsData?: ToleranceSpec[];
  className?: string;
}

export default function Canvas2D({ pdfUrl, balloonsData, className = "" }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [balloons, setBalloons] = useState<Balloon[]>([]);
  const [selectedBalloon, setSelectedBalloon] = useState<number | null>(null);
  const [scale, setScale] = useState(1.0);
  const [pageCount, setPageCount] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [isLoading, setIsLoading] = useState(false);

  // Convert toleranceSpecs to balloons (positioned randomly for now; in production
  // these would come from AS9102 feature extraction with XY coordinates)
  useEffect(() => {
    if (!balloonsData?.length) {
      setBalloons([]);
      return;
    }
    const generated: Balloon[] = balloonsData.map((t, i) => ({
      id: i,
      x: 10 + (i * 15) % 80,
      y: 10 + (i * 22) % 75,
      label: String(i + 1),
      tolerance: t.tolerance,
    }));
    setBalloons(generated);
  }, [balloonsData]);

  // Render PDF page to canvas
  const renderPage = useCallback(async (pageNum: number, scl: number) => {
    if (!pdfUrl || !canvasRef.current) return;

    setIsLoading(true);
    try {
      // Dynamically load pdfjs
      const pdfjsLib = await import("pdfjs-dist");
      pdfjsLib.GlobalWorkerOptions.workerSrc = `//cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjsLib.version}/pdf.worker.min.js`;

      const pdf = await pdfjsLib.getDocument(pdfUrl).promise;
      setPageCount(pdf.numPages);

      const page = await pdf.getPage(pageNum);
      const viewport = page.getViewport({ scale: scl });

      const canvas = canvasRef.current;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      canvas.width  = viewport.width;
      canvas.height = viewport.height;

      await page.render({ canvasContext: ctx as unknown as CanvasRenderingContext2D, viewport, canvas: canvas as unknown as HTMLCanvasElement }).promise;
    } catch (err) {
      console.error("[Canvas2D] PDF render error:", err);
    } finally {
      setIsLoading(false);
    }
  }, [pdfUrl]);

  useEffect(() => {
    if (pdfUrl) renderPage(currentPage, scale);
  }, [pdfUrl, currentPage, scale, renderPage]);

  // Draw balloons as SVG overlay
  const drawBalloons = () =>
    balloons.map((b) => {
      const isSelected = selectedBalloon === b.id;
      return (
        <g
          key={b.id}
          transform={`translate(${b.x}%, ${b.y}%)`}
          style={{ cursor: "pointer" }}
          onClick={() => setSelectedBalloon(isSelected ? null : b.id)}
        >
          <circle
            r={14}
            fill={isSelected ? "#1d4ed8" : "#0f172a"}
            stroke={isSelected ? "#60a5fa" : "#94a3b8"}
            strokeWidth={1.5}
          />
          <text
            textAnchor="middle"
            dominantBaseline="central"
            fill="white"
            fontSize={11}
            fontWeight="bold"
            fontFamily="monospace"
          >
            {b.label}
          </text>
        </g>
      );
    });

  // Placeholder when no PDF
  const renderPlaceholder = () => (
    <div className="flex flex-col items-center justify-center h-full gap-4 text-slate-500">
      <svg className="w-16 h-16 opacity-30" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1}
          d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414A1 1 0 0119 9.414V19a2 2 0 01-2 2z" />
      </svg>
      <p className="text-sm">Upload a 2D drawing to view it here</p>
      <p className="text-xs opacity-60">AS9102 balloons will overlay after inspection</p>
    </div>
  );

  return (
    <div ref={containerRef} className={`relative w-full h-full flex flex-col bg-slate-900 ${className}`}>
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-slate-800 border-b border-slate-700 text-xs text-slate-300 shrink-0">
        <span className="font-medium">2D Drawing</span>
        {pageCount > 0 && (
          <>
            <span className="text-slate-500">|</span>
            <button
              className="hover:text-white disabled:opacity-40"
              onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
              disabled={currentPage <= 1}
            >
              ‹
            </button>
            <span>Page {currentPage} / {pageCount}</span>
            <button
              className="hover:text-white disabled:opacity-40"
              onClick={() => setCurrentPage((p) => Math.min(pageCount, p + 1))}
              disabled={currentPage >= pageCount}
            >
              ›
            </button>
            <span className="text-slate-500">|</span>
            <button onClick={() => setScale((s) => Math.min(3, s + 0.25))} className="hover:text-white">＋</button>
            <span>{Math.round(scale * 100)}%</span>
            <button onClick={() => setScale((s) => Math.max(0.25, s - 0.25))} className="hover:text-white">－</button>
          </>
        )}
        {balloons.length > 0 && (
          <>
            <span className="text-slate-500">|</span>
            <span className="text-cyan-400">{balloons.length} balloons</span>
          </>
        )}
        {isLoading && <span className="text-yellow-400 animate-pulse ml-auto">Loading…</span>}
      </div>

      {/* Canvas area */}
      <div className="flex-1 overflow-auto relative">
        {!pdfUrl ? renderPlaceholder() : (
          <div className="relative inline-block min-w-full min-h-full">
            <canvas ref={canvasRef} className="block" />
            {/* SVG balloon overlay */}
            {balloons.length > 0 && (
              <svg
                className="absolute inset-0 w-full h-full pointer-events-none"
                style={{ pointerEvents: "all" }}
              >
                {drawBalloons()}
              </svg>
            )}
          </div>
        )}
      </div>

      {/* Selected balloon tooltip */}
      {selectedBalloon !== null && (
        <div className="absolute bottom-4 left-4 right-4 bg-slate-800 border border-slate-700 rounded p-2 text-xs text-slate-200">
          <span className="font-semibold text-cyan-400">Balloon {balloons.find((b) => b.id === selectedBalloon)?.label}</span>
          {" — "}
          <span>{balloons.find((b) => b.id === selectedBalloon)?.tolerance ?? "No tolerance specified"}</span>
        </div>
      )}
    </div>
  );
}
