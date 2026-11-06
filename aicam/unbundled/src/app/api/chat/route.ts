import { NextRequest } from "next/server";
import { runRAGPipeline, buildSystemPrompt } from "@/lib/rag/pipeline";
import { createLLMProvider } from "@/lib/providers/llm.provider";

export const runtime = "nodejs";
export const maxDuration = 60;

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { messages } = body as {
      messages: { role: "user" | "assistant"; content: string }[];
    };

    if (!messages || messages.length === 0) {
      return Response.json({ error: "messages required" }, { status: 400 });
    }

    const lastUserMessage = messages.filter((m) => m.role === "user").pop();
    if (!lastUserMessage) {
      return Response.json({ error: "No user message found" }, { status: 400 });
    }

    // Step 1–2: RAG pipeline (hybrid search + rerank)
    const { chunks, citations, contextText } = await runRAGPipeline(lastUserMessage.content);

    // Step 3: Stream LLM response
    const llm = createLLMProvider();
    const systemPrompt = buildSystemPrompt(contextText);

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      async start(controller) {
        try {
          // Send citations metadata first as a special SSE event
          const citationsEvent = `data: ${JSON.stringify({ type: "citations", citations })}\n\n`;
          controller.enqueue(encoder.encode(citationsEvent));

          // Stream LLM tokens
          for await (const token of llm.streamChat({ messages, systemPrompt })) {
            const tokenEvent = `data: ${JSON.stringify({ type: "token", text: token })}\n\n`;
            controller.enqueue(encoder.encode(tokenEvent));
          }

          controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "done" })}\n\n`));
        } catch (err) {
          const msg = err instanceof Error ? err.message : "Stream error";
          controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "error", message: msg })}\n\n`));
        } finally {
          controller.close();
        }
      },
    });

    return new Response(stream, {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Citations-Count": String(citations.length),
      },
    });
  } catch (err) {
    console.error("[/api/chat] Error:", err);
    const message = err instanceof Error ? err.message : "Internal server error";
    return Response.json({ error: message }, { status: 500 });
  }
}
