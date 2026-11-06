"use client";

import { useEffect, useState } from "react";
import { FileText, CheckCircle, Clock, AlertCircle, Layers } from "lucide-react";
import type { Document } from "@/lib/types";

interface Props {
  deploymentMode: string;
}

interface Stats {
  total: number;
  indexed: number;
  processing: number;
  error: number;
}

function Skeleton() {
  return <div className="h-7 w-10 bg-gray-200 rounded animate-pulse mt-0.5" />;
}

export function IngestionStats({ deploymentMode }: Props) {
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function fetchStats() {
      try {
        const res = await fetch("/api/ingest");
        const data = await res.json();
        const docs: Document[] = data.documents ?? [];
        setStats({
          total: docs.length,
          indexed: docs.filter((d) => d.status === "indexed").length,
          processing: docs.filter(
            (d) => d.status === "processing" || d.status === "pending"
          ).length,
          error: docs.filter((d) => d.status === "error").length,
        });
      } catch {
        setStats({ total: 0, indexed: 0, processing: 0, error: 0 });
      } finally {
        setLoading(false);
      }
    }
    fetchStats();
    const iv = setInterval(fetchStats, 10_000);
    return () => clearInterval(iv);
  }, []);

  const cards = [
    {
      label: "Total Documents",
      value: stats?.total,
      icon: FileText,
      color: "text-blue-600",
      bg: "bg-blue-50",
    },
    {
      label: "Indexed",
      value: stats?.indexed,
      icon: CheckCircle,
      color: "text-green-600",
      bg: "bg-green-50",
    },
    {
      label: "Processing",
      value: stats?.processing,
      icon: Clock,
      color: "text-amber-600",
      bg: "bg-amber-50",
    },
    {
      label: "Errors",
      value: stats?.error,
      icon: AlertCircle,
      color: "text-red-600",
      bg: "bg-red-50",
    },
    {
      label: "Deployment",
      value: deploymentMode,
      icon: Layers,
      color: deploymentMode === "CLOUD" ? "text-blue-600" : "text-orange-600",
      bg: deploymentMode === "CLOUD" ? "bg-blue-50" : "bg-orange-50",
      isText: true,
    },
  ];

  return (
    <div className="grid grid-cols-2 sm:grid-cols-5 gap-4">
      {cards.map((card) => (
        <div
          key={card.label}
          className="bg-white rounded-xl border border-gray-200 p-4 flex items-center gap-3 shadow-sm"
        >
          <div className={`p-2 rounded-lg ${card.bg} ${card.color} shrink-0`}>
            <card.icon className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <p className="text-xs text-gray-500">{card.label}</p>
            {"isText" in card && card.isText ? (
              <p className={`text-sm font-bold ${card.color}`}>{card.value}</p>
            ) : loading ? (
              <Skeleton />
            ) : (
              <p className="text-xl font-bold text-gray-900">{card.value ?? 0}</p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
