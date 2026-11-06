import type { RetrievedChunk, Citation, Document } from "../types";
import { createEmbeddingProvider, createRerankProvider, createVectorProvider } from "../providers";

const MAX_RETRIEVED = parseInt(process.env.MAX_RETRIEVED_CHUNKS ?? "20");
const RERANK_TOP_K = parseInt(process.env.RERANK_TOP_K ?? "7");

/**
 * 3-Step Hybrid RAG Retrieval Pipeline:
 * 1. Hybrid search (dense + sparse via RRF in DB)
 * 2. Reranking (Voyage or local BGE-Reranker)
 * 3. Context assembly with citations
 */
export async function runRAGPipeline(query: string): Promise<{
  chunks: RetrievedChunk[];
  citations: Citation[];
  contextText: string;
}> {
  const embeddingProvider = createEmbeddingProvider();
  const rerankProvider = createRerankProvider();
  const vectorProvider = createVectorProvider();

  // Step 1: Embed query + hybrid search
  const queryEmbedding = await embeddingProvider.embed(query);
  const rawChunks = await vectorProvider.hybridSearch({
    queryEmbedding,
    queryText: query,
    limit: MAX_RETRIEVED,
  });

  if (rawChunks.length === 0) {
    return { chunks: [], citations: [], contextText: "" };
  }

  // Step 2: Rerank
  const rerankScores = await rerankProvider.rerank(
    query,
    rawChunks.map((c) => c.content)
  );

  const reranked = rerankScores
    .sort((a, b) => b.score - a.score)
    .slice(0, RERANK_TOP_K)
    .map(({ index, score }) => ({ ...rawChunks[index], rerankScore: score }));

  // Step 3: Fetch documents for citations
  const documentIds = [...new Set(reranked.map((c) => c.documentId))];
  const documents = await Promise.all(documentIds.map((id) => vectorProvider.getDocument(id)));
  const docMap = new Map<string, Document>(
    documents.filter((d): d is Document => d !== null).map((d) => [d.id, d])
  );

  // Build citations and context
  const citations: Citation[] = reranked.map((chunk, i) => {
    const doc = docMap.get(chunk.documentId);
    return {
      index: i + 1,
      chunkId: chunk.chunkId,
      documentId: chunk.documentId,
      documentName: doc?.originalName ?? doc?.filename ?? "Unknown",
      category: doc?.category ?? "GENERAL",
      pageNumber: chunk.pageNumber,
      snippet: chunk.content.slice(0, 300) + (chunk.content.length > 300 ? "…" : ""),
      downloadUrl: `/api/files/download/${chunk.documentId}`,
      viewUrl: `/api/files/view/${chunk.documentId}?page=${chunk.pageNumber ?? 1}`,
    };
  });

  const contextText = reranked
    .map((chunk, i) => `[${i + 1}] (Page ${chunk.pageNumber ?? "N/A"}, Doc: ${docMap.get(chunk.documentId)?.originalName ?? chunk.documentId})\n${chunk.content}`)
    .join("\n\n---\n\n");

  return { chunks: reranked, citations, contextText };
}

export function buildSystemPrompt(contextText: string): string {
  return `You are an expert Manufacturing AI Assistant for an enterprise factory.
You have access to indexed manufacturing documents including 2D drawings, inspection reports, purchase orders, inventory logs, and tool catalogs.

Use ONLY the context provided below to answer questions. Cite sources using [1], [2], [3] notation corresponding to the numbered excerpts.
If the answer is not in the context, explicitly state: "This information is not available in the indexed documents."
Do not make up part numbers, tolerances, specifications, or any manufacturing data.

Be precise, technical, and concise. Format tables in markdown when presenting structured data.

--- CONTEXT ---
${contextText}
--- END CONTEXT ---`;
}
