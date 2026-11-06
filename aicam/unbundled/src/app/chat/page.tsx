import { ChatInterface } from "@/components/chat/ChatInterface";
import { Settings, Database } from "lucide-react";
import Link from "next/link";

export const metadata = { title: "AI Chat | Manufacturing Intelligence" };

export default function ChatPage() {
  return (
    <div className="flex flex-col h-screen bg-gray-50">
      {/* Header */}
      <header className="flex-shrink-0 border-b border-gray-200 bg-white">
        <div className="px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="h-8 w-8 rounded-lg bg-blue-600 flex items-center justify-center">
              <span className="text-white text-xs font-bold">AI</span>
            </div>
            <div>
              <h1 className="text-sm font-semibold text-gray-900">Manufacturing Intelligence</h1>
              <p className="text-xs text-gray-500">Enterprise RAG Search · Grounded Answers</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Link
              href="/admin/ingestion"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-300 text-xs text-gray-700 hover:bg-gray-50 transition-colors"
            >
              <Database className="h-3.5 w-3.5" />
              Manage Docs
            </Link>
          </div>
        </div>
      </header>

      {/* Chat area fills remaining space */}
      <div className="flex-1 overflow-hidden">
        <ChatInterface />
      </div>
    </div>
  );
}
