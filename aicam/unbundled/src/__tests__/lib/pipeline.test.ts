import { describe, it, expect, vi, afterEach } from "vitest";
import { buildSystemPrompt, runRAGPipeline } from "@/lib/rag/pipeline";
import {
  createEmbeddingProvider,
  createRerankProvider,
  createVectorProvider,
} from "@/lib/providers";

// Mock all provider factories so tests don't make real HTTP calls
vi.mock("@/lib/providers", () => ({
  createEmbeddingProvider: vi.fn(),
  createRerankProvider: vi.fn(),
  createVectorProvider: vi.fn(),
}));

afterEach(() => {
  vi.clearAllMocks();
});

// ─────────────────────────────────────────────────────────────────────────────
// buildSystemPrompt — pure function, no mocking needed
// ─────────────────────────────────────────────────────────────────────────────

describe("buildSystemPrompt", () => {
  it("includes the provided context text verbatim", () => {
    const context = "[1] (Page 3, Doc: Bracket-Rev-A.pdf)\nTolerance: ±0.005 inch on all bores.";
    const prompt = buildSystemPrompt(context);
    expect(prompt).toContain(context);
  });

  it("includes manufacturing assistant role description", () => {
    const prompt = buildSystemPrompt("some context");
    expect(prompt.toLowerCase()).toContain("manufacturing");
    expect(prompt.toLowerCase()).toContain("assistant");
  });

  it("instructs the model to cite sources with [N] notation", () => {
    const prompt = buildSystemPrompt("ctx");
    expect(prompt).toContain("[1]");
    expect(prompt.toLowerCase()).toContain("cite");
  });

  it("instructs model to state when information is not available", () => {
    const prompt = buildSystemPrompt("ctx");
    expect(prompt).toContain("not available in the indexed documents");
  });

  it("instructs model NOT to fabricate manufacturing data", () => {
    const prompt = buildSystemPrompt("ctx");
    expect(prompt).toContain("Do not make up");
  });

  it("wraps context in CONTEXT delimiters", () => {
    const prompt = buildSystemPrompt("test context data");
    expect(prompt).toContain("--- CONTEXT ---");
    expect(prompt).toContain("--- END CONTEXT ---");
  });

  it("context appears between the delimiters", () => {
    const context = "unique_content_xyz_12345";
    const prompt = buildSystemPrompt(context);
    const contextStart = prompt.indexOf("--- CONTEXT ---");
    const contextEnd = prompt.indexOf("--- END CONTEXT ---");
    const contentPos = prompt.indexOf(context);
    expect(contextStart).toBeLessThan(contentPos);
    expect(contentPos).toBeLessThan(contextEnd);
  });

  it("handles empty context without crashing", () => {
    expect(() => buildSystemPrompt("")).not.toThrow();
    const prompt = buildSystemPrompt("");
    expect(prompt).toContain("--- CONTEXT ---");
  });

  it("mentions document types it has access to", () => {
    const prompt = buildSystemPrompt("ctx");
    expect(prompt.toLowerCase()).toMatch(/drawing|inspection|purchase|tool|inventory/);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// runRAGPipeline — integration with mocked providers
// ─────────────────────────────────────────────────────────────────────────────

const EMPTY_VECTOR_PROVIDER = {
  upsertChunk: vi.fn(),
  deleteByDocumentId: vi.fn(),
  upsertDocument: vi.fn(),
  updateDocumentStatus: vi.fn(),
  listDocuments: vi.fn(),
};

describe("runRAGPipeline (mocked providers)", () => {
  it("returns empty results when hybridSearch finds no chunks", async () => {
    vi.mocked(createEmbeddingProvider).mockReturnValue({
      embed: vi.fn().mockResolvedValue(new Array(1024).fill(0.1)),
      embedBatch: vi.fn(),
      dimensions: 1024,
    });
    vi.mocked(createRerankProvider).mockReturnValue({
      rerank: vi.fn().mockResolvedValue([]),
    });
    vi.mocked(createVectorProvider).mockReturnValue({
      ...EMPTY_VECTOR_PROVIDER,
      hybridSearch: vi.fn().mockResolvedValue([]),
      getDocument: vi.fn(),
    });

    const result = await runRAGPipeline("what is the tolerance for part BRK-001?");

    expect(result.chunks).toHaveLength(0);
    expect(result.citations).toHaveLength(0);
    expect(result.contextText).toBe("");
  });

  it("builds citations correctly from retrieved chunks and documents", async () => {
    const mockDoc = {
      id: "doc-abc",
      filename: "bracket-drawing-rev-c.pdf",
      originalName: "Bracket Drawing Rev C.pdf",
      category: "DRAWING_2D" as const,
      storagePath: "DRAWING_2D/bracket.pdf",
      storageBucket: "manufacturing-docs",
      status: "indexed" as const,
      metadata: {},
      autoExtracted: {},
      createdAt: "2024-01-01T00:00:00Z",
      updatedAt: "2024-01-01T00:00:00Z",
    };

    const mockChunks = [
      {
        chunkId: "chunk-1",
        documentId: "doc-abc",
        content: "Tolerance for bore: ±0.005 inch. Surface finish Ra 1.6 µm required.",
        pageNumber: 4,
        contentType: "text",
        rrfScore: 0.92,
        denseRank: 1,
        sparseRank: 2,
      },
    ];

    vi.mocked(createEmbeddingProvider).mockReturnValue({
      embed: vi.fn().mockResolvedValue(new Array(1024).fill(0.2)),
      embedBatch: vi.fn(),
      dimensions: 1024,
    });
    vi.mocked(createRerankProvider).mockReturnValue({
      rerank: vi.fn().mockResolvedValue([{ index: 0, score: 0.95 }]),
    });
    vi.mocked(createVectorProvider).mockReturnValue({
      ...EMPTY_VECTOR_PROVIDER,
      hybridSearch: vi.fn().mockResolvedValue(mockChunks),
      getDocument: vi.fn().mockResolvedValue(mockDoc),
    });

    const result = await runRAGPipeline("tolerance for bore on bracket");

    expect(result.chunks).toHaveLength(1);
    expect(result.citations).toHaveLength(1);

    const citation = result.citations[0];
    expect(citation.index).toBe(1);
    expect(citation.documentName).toBe("Bracket Drawing Rev C.pdf");
    expect(citation.category).toBe("DRAWING_2D");
    expect(citation.pageNumber).toBe(4);
    expect(citation.snippet).toContain("Tolerance for bore");
    expect(citation.downloadUrl).toBe("/api/files/download/doc-abc");
  });

  it("truncates snippet at 300 characters and appends ellipsis", async () => {
    const longContent = "A".repeat(400);

    vi.mocked(createEmbeddingProvider).mockReturnValue({
      embed: vi.fn().mockResolvedValue(new Array(1024).fill(0)),
      embedBatch: vi.fn(),
      dimensions: 1024,
    });
    vi.mocked(createRerankProvider).mockReturnValue({
      rerank: vi.fn().mockResolvedValue([{ index: 0, score: 0.8 }]),
    });
    vi.mocked(createVectorProvider).mockReturnValue({
      ...EMPTY_VECTOR_PROVIDER,
      hybridSearch: vi.fn().mockResolvedValue([
        {
          chunkId: "c1",
          documentId: "d1",
          content: longContent,
          pageNumber: 1,
          rrfScore: 0.9,
          denseRank: 1,
          sparseRank: 1,
        },
      ]),
      getDocument: vi.fn().mockResolvedValue({
        id: "d1",
        originalName: "doc.pdf",
        category: "GENERAL" as const,
        metadata: {},
        createdAt: "2024-01-01T00:00:00Z",
        updatedAt: "2024-01-01T00:00:00Z",
      }),
    });

    const result = await runRAGPipeline("query");

    expect(result.citations[0].snippet).toHaveLength(301); // 300 chars + "…"
    expect(result.citations[0].snippet.endsWith("…")).toBe(true);
  });

  it("contextText includes numbered references with page and doc name", async () => {
    vi.mocked(createEmbeddingProvider).mockReturnValue({
      embed: vi.fn().mockResolvedValue(new Array(1024).fill(0)),
      embedBatch: vi.fn(),
      dimensions: 1024,
    });
    vi.mocked(createRerankProvider).mockReturnValue({
      rerank: vi.fn().mockResolvedValue([{ index: 0, score: 0.9 }]),
    });
    vi.mocked(createVectorProvider).mockReturnValue({
      ...EMPTY_VECTOR_PROVIDER,
      hybridSearch: vi.fn().mockResolvedValue([
        {
          chunkId: "cx1",
          documentId: "dx1",
          content: "Material: Aluminum 6061-T6",
          pageNumber: 2,
          rrfScore: 0.88,
          denseRank: 1,
          sparseRank: 1,
        },
      ]),
      getDocument: vi.fn().mockResolvedValue({
        id: "dx1",
        originalName: "Material Spec.pdf",
        category: "TOOLING_MATERIAL" as const,
        metadata: {},
        createdAt: "2024-01-01T00:00:00Z",
        updatedAt: "2024-01-01T00:00:00Z",
      }),
    });

    const result = await runRAGPipeline("material specification");

    expect(result.contextText).toContain("[1]");
    expect(result.contextText).toContain("Page 2");
    expect(result.contextText).toContain("Material Spec.pdf");
    expect(result.contextText).toContain("Material: Aluminum 6061-T6");
  });

  it("respects RERANK_TOP_K limit on returned chunks", async () => {
    // With default RERANK_TOP_K=7, even if 10 chunks returned from search,
    // only 7 should appear in citations after reranking
    const mockChunks = Array.from({ length: 10 }, (_, i) => ({
      chunkId: `chunk-${i}`,
      documentId: `doc-${i}`,
      content: `Content ${i}`,
      pageNumber: i + 1,
      rrfScore: 1 - i * 0.05,
      denseRank: i + 1,
      sparseRank: i + 1,
    }));

    const mockRerankScores = mockChunks.map((_, i) => ({ index: i, score: 1 - i * 0.05 }));

    vi.mocked(createEmbeddingProvider).mockReturnValue({
      embed: vi.fn().mockResolvedValue(new Array(1024).fill(0)),
      embedBatch: vi.fn(),
      dimensions: 1024,
    });
    vi.mocked(createRerankProvider).mockReturnValue({
      rerank: vi.fn().mockResolvedValue(mockRerankScores),
    });
    vi.mocked(createVectorProvider).mockReturnValue({
      ...EMPTY_VECTOR_PROVIDER,
      hybridSearch: vi.fn().mockResolvedValue(mockChunks),
      getDocument: vi.fn().mockResolvedValue(null), // null → fallback to chunk.documentId
    });

    const result = await runRAGPipeline("general query");

    // RERANK_TOP_K defaults to 7
    expect(result.chunks.length).toBeLessThanOrEqual(7);
    expect(result.citations.length).toBeLessThanOrEqual(7);
  });
});
