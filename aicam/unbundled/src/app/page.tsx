import Link from "next/link";
import {
  MessageSquare,
  UploadCloud,
  Database,
  Zap,
  Cpu,
  ArrowRight,
  LayoutDashboard,
  Settings,
} from "lucide-react";
import { LiveStats } from "@/components/home/LiveStats";
import { RecentDocuments } from "@/components/home/RecentDocuments";

export const metadata = { title: "Manufacturing AI Intelligence" };

export default function Home() {
  const deploymentMode = process.env.DEPLOYMENT_MODE ?? "CLOUD";

  return (
    <div className="min-h-screen bg-gray-50">
      {/* ── Sticky Navigation Header ─────────────────────────────────────── */}
      <header className="sticky top-0 z-50 border-b border-gray-200 bg-white/95 backdrop-blur-sm shadow-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="h-8 w-8 rounded-lg bg-blue-600 flex items-center justify-center shrink-0">
              <Zap className="h-4 w-4 text-white" />
            </div>
            <span className="font-bold text-gray-900 hidden sm:block">Manufacturing AI</span>
          </div>

          <nav className="flex items-center gap-0.5">
            <Link
              href="/chat"
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-md transition-colors"
            >
              <MessageSquare className="h-3.5 w-3.5" />
              <span className="hidden sm:block">Chat</span>
            </Link>
            <Link
              href="/admin/ingestion"
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-md transition-colors"
            >
              <UploadCloud className="h-3.5 w-3.5" />
              <span className="hidden sm:block">Documents</span>
            </Link>
            <Link
              href="/studio"
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-md transition-colors"
            >
              <Cpu className="h-3.5 w-3.5" />
              <span className="hidden sm:block">Studio</span>
            </Link>
            <Link
              href="/admin/system"
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-md transition-colors"
            >
              <Settings className="h-3.5 w-3.5" />
              <span className="hidden sm:block">System</span>
            </Link>
          </nav>

          <span
            className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold shrink-0 ${
              deploymentMode === "CLOUD"
                ? "bg-blue-100 text-blue-800"
                : "bg-orange-100 text-orange-800"
            }`}
          >
            <span className="h-1.5 w-1.5 rounded-full bg-current" />
            {deploymentMode}
          </span>
        </div>
      </header>

      {/* ── Hero ─────────────────────────────────────────────────────────── */}
      <div className="bg-gradient-to-br from-blue-700 via-blue-600 to-indigo-700 text-white">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-12 sm:py-16">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-6">
            <div>
              <div className="flex items-center gap-2 mb-3">
                <LayoutDashboard className="h-5 w-5 text-blue-300" />
                <span className="text-blue-300 text-sm font-medium">Dashboard</span>
              </div>
              <h1 className="text-3xl sm:text-4xl font-bold mb-3 leading-tight">
                Manufacturing AI
                <br />
                Intelligence Platform
              </h1>
              <p className="text-blue-200 text-base max-w-xl leading-relaxed">
                Enterprise Multi-Modal RAG System for 2D drawings, inspection reports,
                purchase orders, inventory, tooling &amp; more.
              </p>
            </div>
            <div className="flex flex-col sm:flex-row gap-3 shrink-0">
              <Link
                href="/chat"
                className="inline-flex items-center justify-center gap-2 bg-white text-blue-700 font-semibold text-sm px-5 py-2.5 rounded-lg hover:bg-blue-50 transition-colors shadow-sm"
              >
                <MessageSquare className="h-4 w-4" />
                Ask AI
              </Link>
              <Link
                href="/admin/ingestion"
                className="inline-flex items-center justify-center gap-2 bg-blue-800/60 border border-blue-500/60 text-white font-semibold text-sm px-5 py-2.5 rounded-lg hover:bg-blue-800/80 transition-colors"
              >
                <UploadCloud className="h-4 w-4" />
                Upload Docs
              </Link>
            </div>
          </div>
        </div>
      </div>

      {/* ── Main Content ─────────────────────────────────────────────────── */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-8 space-y-8">

        {/* Live Stats */}
        <LiveStats />

        {/* Action Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <Link
            href="/chat"
            className="group rounded-2xl border border-gray-200 bg-white p-6 shadow-sm hover:shadow-md hover:border-blue-300 transition-all"
          >
            <div className="flex items-center gap-3 mb-3">
              <div className="h-10 w-10 rounded-xl bg-blue-100 flex items-center justify-center group-hover:bg-blue-600 transition-colors">
                <MessageSquare className="h-5 w-5 text-blue-600 group-hover:text-white transition-colors" />
              </div>
              <div>
                <h2 className="font-semibold text-gray-900">AI Chatbot</h2>
                <p className="text-xs text-gray-400">RAG-powered Q&amp;A</p>
              </div>
            </div>
            <p className="text-sm text-gray-500">
              Ask questions about any indexed manufacturing document with citation-backed answers.
            </p>
            <div className="mt-4 flex items-center gap-1 text-xs font-medium text-blue-600 group-hover:text-blue-700">
              Open Chat <ArrowRight className="h-3 w-3" />
            </div>
          </Link>

          <Link
            href="/admin/ingestion"
            className="group rounded-2xl border border-gray-200 bg-white p-6 shadow-sm hover:shadow-md hover:border-purple-300 transition-all"
          >
            <div className="flex items-center gap-3 mb-3">
              <div className="h-10 w-10 rounded-xl bg-purple-100 flex items-center justify-center group-hover:bg-purple-600 transition-colors">
                <UploadCloud className="h-5 w-5 text-purple-600 group-hover:text-white transition-colors" />
              </div>
              <div>
                <h2 className="font-semibold text-gray-900">Ingest Documents</h2>
                <p className="text-xs text-gray-400">PDF, Excel, DOCX, images</p>
              </div>
            </div>
            <p className="text-sm text-gray-500">
              Upload drawings, inspection reports, POs, and more to index for AI-powered search.
            </p>
            <div className="mt-4 flex items-center gap-1 text-xs font-medium text-purple-600 group-hover:text-purple-700">
              Manage Library <ArrowRight className="h-3 w-3" />
            </div>
          </Link>

          <Link
            href="/studio"
            className="group rounded-2xl border border-gray-200 bg-white p-6 shadow-sm hover:shadow-md hover:border-cyan-300 transition-all"
          >
            <div className="flex items-center gap-3 mb-3">
              <div className="h-10 w-10 rounded-xl bg-cyan-100 flex items-center justify-center group-hover:bg-cyan-600 transition-colors">
                <Cpu className="h-5 w-5 text-cyan-600 group-hover:text-white transition-colors" />
              </div>
              <div>
                <h2 className="font-semibold text-gray-900">CAPP Studio</h2>
                <p className="text-xs text-gray-400">AI Process Planning + G-Code</p>
              </div>
            </div>
            <p className="text-sm text-gray-500">
              Upload STEP / 2D drawings, generate machining plans, visualize 3D toolpath, and complete AS9102 FAI.
            </p>
            <div className="mt-4 flex items-center gap-1 text-xs font-medium text-cyan-600 group-hover:text-cyan-700">
              Open Studio <ArrowRight className="h-3 w-3" />
            </div>
          </Link>
        </div>

        {/* Recent Files Section */}
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-base font-semibold text-gray-900">Recently Indexed Files</h2>
            <Link
              href="/admin/ingestion"
              className="text-sm text-blue-600 hover:text-blue-800 flex items-center gap-1 font-medium"
            >
              View all <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>
          <RecentDocuments />
        </section>

        {/* System Capabilities */}
        <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm">
          <h3 className="font-semibold text-gray-900 mb-4 flex items-center gap-2">
            <Database className="h-4 w-4 text-gray-400" />
            System Capabilities
          </h3>
          <ul className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-2 text-sm text-gray-600">
            {[
              "Hybrid Search: Dense embeddings + BM25 keyword via RRF fusion",
              "Multi-modal: PDF, Excel tables, DOCX, engineering drawings, images",
              "Citation-backed answers with page references and download links",
              "Streaming LLM responses with grounded context enforcement",
              "Cloud (Anthropic/Voyage/Supabase) or On-Prem (Ollama/MinIO/Postgres)",
              "Category tagging: Drawings, FIRs, POs, Inventory, Tooling, General",
              "CAPP Studio: AI process planning + G-code generation + 3D toolpath viz",
              "AS9102 FAI inspection forms, DNC transfer, ITAR/AS9100 audit trail",
            ].map((feat) => (
              <li key={feat} className="flex items-start gap-2">
                <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-blue-600 shrink-0" />
                {feat}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
