"use client";

import { useState, useRef, useEffect } from "react";
import { MessageBubble } from "./MessageBubble";
import { ChatInput } from "./ChatInput";
import type { ChatMessage, Citation } from "@/lib/types";
import { Bot } from "lucide-react";

export function ChatInterface() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const sendMessage = async () => {
    const userContent = input.trim();
    if (!userContent || isStreaming) return;

    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: userContent,
      createdAt: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setIsStreaming(true);

    const assistantId = crypto.randomUUID();
    const assistantMsg: ChatMessage = {
      id: assistantId,
      role: "assistant",
      content: "",
      citations: [],
      createdAt: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, assistantMsg]);

    try {
      const allMessages = [...messages, userMsg].map((m) => ({
        role: m.role,
        content: m.content,
      }));

      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: allMessages }),
      });

      if (!response.body) throw new Error("No response body");

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let citations: Citation[] = [];
      let content = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const event = JSON.parse(line.slice(6)) as {
              type: "citations" | "token" | "done" | "error";
              citations?: Citation[];
              text?: string;
              message?: string;
            };

            if (event.type === "citations") {
              citations = event.citations ?? [];
              setMessages((prev) =>
                prev.map((m) => (m.id === assistantId ? { ...m, citations } : m))
              );
            } else if (event.type === "token") {
              content += event.text ?? "";
              setMessages((prev) =>
                prev.map((m) => (m.id === assistantId ? { ...m, content } : m))
              );
            } else if (event.type === "error") {
              content += `\n\n*Error: ${event.message}*`;
              setMessages((prev) =>
                prev.map((m) => (m.id === assistantId ? { ...m, content } : m))
              );
            }
          } catch {}
        }
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Connection failed";
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? { ...m, content: `*Sorry, something went wrong: ${message}*` }
            : m
        )
      );
    } finally {
      setIsStreaming(false);
    }
  };

  const isEmpty = messages.length === 0;

  return (
    <div className="flex flex-col h-full">
      {/* Message List */}
      <div className="flex-1 overflow-y-auto px-4 py-6">
        {isEmpty ? (
          <div className="flex flex-col items-center justify-center h-full text-center space-y-4">
            <div className="h-16 w-16 rounded-2xl bg-blue-600 flex items-center justify-center">
              <Bot className="h-8 w-8 text-white" />
            </div>
            <div>
              <h2 className="text-xl font-semibold text-gray-900">Manufacturing AI Assistant</h2>
              <p className="text-sm text-gray-500 mt-1 max-w-md">
                Ask about drawings, inspection reports, purchase orders, inventory, or any indexed manufacturing document.
              </p>
            </div>
          </div>
        ) : (
          <div className="max-w-4xl mx-auto space-y-6">
            {messages.map((msg, i) => (
              <MessageBubble
                key={msg.id}
                message={msg}
                isStreaming={isStreaming && i === messages.length - 1 && msg.role === "assistant"}
              />
            ))}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input Area */}
      <div className="border-t border-gray-200 bg-white px-4 py-4">
        <div className="max-w-4xl mx-auto">
          <ChatInput
            value={input}
            onChange={setInput}
            onSubmit={sendMessage}
            disabled={isStreaming}
            showSuggestions={isEmpty}
          />
        </div>
      </div>
    </div>
  );
}
