"use client";

import { useRef, useCallback } from "react";
import { Send } from "lucide-react";
import { cn } from "@/lib/utils";

const SUGGESTIONS = [
  "What are the tolerances for part DWG-A901?",
  "Show me the latest inspection results for batch #550",
  "What is the current stock level for material grade EN8?",
  "List all open purchase orders above ₹50,000",
];

interface ChatInputProps {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  disabled?: boolean;
  showSuggestions?: boolean;
}

export function ChatInput({ value, onChange, onSubmit, disabled, showSuggestions }: ChatInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!disabled && value.trim()) onSubmit();
    }
  };

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    onChange(e.target.value);
    // Auto-resize
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = "auto";
      ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`;
    }
  };

  return (
    <div className="space-y-3">
      {/* Suggestion chips */}
      {showSuggestions && (
        <div className="flex flex-wrap gap-2">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              onClick={() => onChange(s)}
              className="rounded-full border border-gray-300 px-3 py-1.5 text-xs text-gray-600 hover:border-blue-400 hover:text-blue-700 hover:bg-blue-50 transition-colors"
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {/* Input row */}
      <div className="flex items-end gap-3 rounded-2xl border border-gray-300 bg-white px-4 py-3 shadow-sm focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-100 transition-all">
        <textarea
          ref={textareaRef}
          rows={1}
          value={value}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder="Ask about drawings, inspection reports, inventory, purchase orders…"
          className="flex-1 resize-none bg-transparent text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none disabled:opacity-50"
          style={{ minHeight: "24px", maxHeight: "160px" }}
        />
        <button
          onClick={onSubmit}
          disabled={disabled || !value.trim()}
          className={cn(
            "flex-shrink-0 h-8 w-8 rounded-full flex items-center justify-center transition-colors",
            disabled || !value.trim()
              ? "bg-gray-100 text-gray-400 cursor-not-allowed"
              : "bg-blue-600 text-white hover:bg-blue-700"
          )}
        >
          <Send className="h-4 w-4" />
        </button>
      </div>
      <p className="text-xs text-gray-400 text-center">
        Press Enter to send · Shift+Enter for new line
      </p>
    </div>
  );
}
