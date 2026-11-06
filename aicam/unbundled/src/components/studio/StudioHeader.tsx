"use client";

import Link from "next/link";

type ViewMode = "3d" | "2d" | "fai";

interface Props {
  viewMode: ViewMode;
  onViewModeChange: (m: ViewMode) => void;
  partName?: string;
  isPlaying?: boolean;
  onPlayToggle?: () => void;
}

export default function StudioHeader({
  viewMode,
  onViewModeChange,
  partName,
  isPlaying,
  onPlayToggle,
}: Props) {
  const views: { id: ViewMode; label: string }[] = [
    { id: "3d",  label: "3D Toolpath" },
    { id: "2d",  label: "2D Drawing" },
    { id: "fai", label: "AS9102 FAI" },
  ];

  return (
    <header className="flex items-center h-11 px-4 gap-4 bg-slate-900 border-b border-slate-700 shrink-0 z-20">
      {/* Logo / back */}
      <Link href="/" className="flex items-center gap-2 text-slate-400 hover:text-slate-200 transition-colors">
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
        </svg>
        <span className="text-xs font-medium">AI CAD/CAM</span>
      </Link>

      <div className="h-4 w-px bg-slate-700" />

      {/* Part name */}
      <span className="text-xs text-slate-300 font-medium truncate max-w-40">
        {partName ?? "No part loaded"}
      </span>

      <div className="h-4 w-px bg-slate-700" />

      {/* View mode switcher */}
      <div className="flex rounded-md border border-slate-700 overflow-hidden text-xs">
        {views.map((v) => (
          <button
            key={v.id}
            onClick={() => onViewModeChange(v.id)}
            className={`px-3 py-1 transition-colors ${
              viewMode === v.id
                ? "bg-cyan-600 text-white"
                : "bg-slate-800 text-slate-400 hover:text-slate-200"
            }`}
          >
            {v.label}
          </button>
        ))}
      </div>

      {/* Play/stop for 3D animation */}
      {viewMode === "3d" && (
        <button
          onClick={onPlayToggle}
          className={`flex items-center gap-1.5 px-3 py-1 text-xs rounded border transition-colors ${
            isPlaying
              ? "border-red-600 text-red-400 hover:bg-red-950"
              : "border-slate-600 text-slate-400 hover:text-slate-200"
          }`}
        >
          {isPlaying ? (
            <>
              <span className="w-2 h-2 bg-red-400 rounded-sm inline-block" />
              Stop
            </>
          ) : (
            <>
              <span className="inline-block w-0 h-0 border-t-[5px] border-b-[5px] border-l-[8px] border-transparent border-l-slate-400" />
              Simulate
            </>
          )}
        </button>
      )}

      {/* Spacer */}
      <div className="flex-1" />

      {/* Keyboard hints */}
      <div className="text-[10px] text-slate-500 hidden lg:flex gap-3">
        <span>Click 3D line → sync editor</span>
        <span>|</span>
        <span>Click editor line → sync 3D</span>
      </div>
    </header>
  );
}
