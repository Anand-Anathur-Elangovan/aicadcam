"use client";

import { useState } from "react";
import { FolderOpen, RefreshCw, CheckCircle, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";

interface SyncStatus {
  active: boolean;
  message: string;
  type: "idle" | "success" | "error" | "running";
}

export function DirectorySync() {
  const [path, setPath] = useState(process.env.NEXT_PUBLIC_DEFAULT_WATCH_DIR ?? "");
  const [status, setStatus] = useState<SyncStatus>({ active: false, message: "", type: "idle" });

  const startSync = async () => {
    if (!path.trim()) return;
    setStatus({ active: true, message: "Scanning directory…", type: "running" });

    try {
      const res = await fetch("/api/ingest/watch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ directory: path }),
      });

      if (!res.ok) {
        const err = await res.json();
        setStatus({ active: false, message: err.error ?? "Failed to start watcher", type: "error" });
        return;
      }

      setStatus({
        active: true,
        message: `Watching ${path} for new documents…`,
        type: "success",
      });
    } catch (err) {
      setStatus({
        active: false,
        message: err instanceof Error ? err.message : "Connection failed",
        type: "error",
      });
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          Local / Network Directory Path
        </label>
        <div className="flex gap-2">
          <div className="relative flex-1">
            <FolderOpen className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
            <input
              type="text"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="/mnt/manufacturing_data or \\192.168.1.100\Docs"
              className="w-full pl-9 pr-3 h-9 rounded-md border border-gray-300 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <Button onClick={startSync} disabled={!path.trim() || status.type === "running"}>
            {status.type === "running" ? (
              <RefreshCw className="h-4 w-4 animate-spin" />
            ) : (
              "Start Sync"
            )}
          </Button>
        </div>
        <p className="mt-1 text-xs text-gray-500">
          The system will recursively scan and continuously monitor this path for new or changed files.
        </p>
      </div>

      {status.message && (
        <div
          className={`flex items-center gap-2 rounded-lg px-4 py-3 text-sm ${
            status.type === "success"
              ? "bg-green-50 text-green-800"
              : status.type === "error"
              ? "bg-red-50 text-red-800"
              : "bg-blue-50 text-blue-800"
          }`}
        >
          {status.type === "success" && <CheckCircle className="h-4 w-4 shrink-0" />}
          {status.type === "error" && <XCircle className="h-4 w-4 shrink-0" />}
          {status.type === "running" && <RefreshCw className="h-4 w-4 shrink-0 animate-spin" />}
          {status.message}
        </div>
      )}
    </div>
  );
}
