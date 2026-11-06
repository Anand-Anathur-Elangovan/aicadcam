import { FileUploadZone } from "@/components/admin/FileUploadZone";
import { DirectorySync } from "@/components/admin/DirectorySync";
import { DocumentList } from "@/components/admin/DocumentList";
import { IngestionStats } from "@/components/admin/IngestionStats";
import { UploadCloud, FolderSync, Database, ArrowLeft } from "lucide-react";
import Link from "next/link";

export const metadata = { title: "Document Ingestion | Manufacturing AI" };

export default function IngestionPage() {
  const deploymentMode = process.env.DEPLOYMENT_MODE ?? "CLOUD";

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="border-b border-gray-200 bg-white sticky top-0 z-40 shadow-sm">
        <div className="max-w-6xl mx-auto px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link
              href="/"
              className="text-gray-400 hover:text-gray-600 transition-colors p-1 rounded"
              title="Back to Home"
            >
              <ArrowLeft className="h-4 w-4" />
            </Link>
            <div>
              <h1 className="text-base font-bold text-gray-900">Document Library</h1>
              <p className="text-xs text-gray-500">Upload and index manufacturing documents for AI search</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <span
              className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${
                deploymentMode === "CLOUD"
                  ? "bg-blue-100 text-blue-800"
                  : "bg-orange-100 text-orange-800"
              }`}
            >
              <span className="h-1.5 w-1.5 rounded-full bg-current" />
              {deploymentMode} MODE
            </span>
          </div>
        </div>
      </header>

      <div className="max-w-6xl mx-auto px-6 py-8 space-y-8">
        {/* Live Stats Row — client component */}
        <IngestionStats deploymentMode={deploymentMode} />

        {/* Upload Section */}
        <section className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm">
          <div className="flex items-center gap-2 mb-5">
            <UploadCloud className="h-5 w-5 text-blue-600" />
            <h2 className="text-base font-semibold text-gray-900">Upload Documents</h2>
          </div>
          <FileUploadZone />
        </section>

        {/* Directory Sync — ON_PREM only */}
        {deploymentMode === "ON_PREM" && (
          <section className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm">
            <div className="flex items-center gap-2 mb-5">
              <FolderSync className="h-5 w-5 text-orange-600" />
              <h2 className="text-base font-semibold text-gray-900">Local / Network Folder Sync</h2>
            </div>
            <DirectorySync />
          </section>
        )}

        {/* Document Library */}
        <section className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm">
          <div className="flex items-center gap-2 mb-5">
            <Database className="h-5 w-5 text-purple-600" />
            <h2 className="text-base font-semibold text-gray-900">Document Library</h2>
          </div>
          <DocumentList />
        </section>
      </div>
    </div>
  );
}
