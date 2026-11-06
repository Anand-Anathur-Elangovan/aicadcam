import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
  // Clear global pg pool singleton between tests
  (globalThis as Record<string, unknown>).__pgPool = undefined;
});

// Hardcoded fixture data matching the DB schema
const DOC_ROW = {
  id: "uuid-doc-001",
  filename: "bracket-rev-c.pdf",
  original_name: "Bracket Rev C.pdf",
  category: "DRAWING_2D",
  mime_type: "application/pdf",
  file_size: 45678,
  storage_path: "DRAWING_2D/bracket-rev-c.pdf",
  storage_bucket: "manufacturing-docs",
  page_count: 3,
  status: "indexed",
  error_message: null,
  doc_title: "Bracket Assembly Drawing",
  description: "Front-face bracket",
  part_name: "Main Bracket",
  part_number: "BRK-001",
  revision: "C",
  developed_date: "2024-01-15",
  customer_name: "Acme Corp",
  issue_details: null,
  auto_extracted: {},
  metadata: {},
  source_path: null,
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-02T00:00:00Z",
};

const HYBRID_ROW = {
  chunk_id: "chunk-xyz",
  document_id: "uuid-doc-001",
  content: "Tolerance: ±0.005 inch on all bores.",
  page_number: 2,
  content_type: "text",
  rrf_score: 0.874,
  dense_rank: 1,
  sparse_rank: 3,
};

// ─────────────────────────────────────────────────────────────────────────────
// LocalPostgresVectorProvider — tests with mocked pg Pool
// ─────────────────────────────────────────────────────────────────────────────

describe("LocalPostgresVectorProvider", () => {
  async function buildProvider(queryReturn: { rows: unknown[] } = { rows: [] }) {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");
    vi.stubEnv("ONPREM_POSTGRES_URL", "postgresql://postgres:postgres@localhost:5432/test_db");

    const mockQuery = vi.fn().mockResolvedValue(queryReturn);

    vi.doMock("pg", () => ({
      // class syntax required for `new Pool()`
      Pool: class MockPool {
        query = mockQuery;
      },
    }));

    const { createVectorProvider } = await import("@/lib/providers/vector.provider");
    return { provider: createVectorProvider(), mockQuery };
  }

  it("upsertDocument() returns the document ID from DB", async () => {
    const { provider, mockQuery } = await buildProvider({ rows: [{ id: "new-doc-id-abc" }] });

    const id = await provider.upsertDocument({
      filename: "bracket.pdf",
      originalName: "Bracket Assembly.pdf",
      category: "DRAWING_2D",
      mimeType: "application/pdf",
      fileSize: 45000,
      storagePath: "DRAWING_2D/bracket.pdf",
      storageBucket: "manufacturing-docs",
      status: "processing",
      partNumber: "BRK-001",
      revision: "A",
      metadata: {},
      autoExtracted: {},
    });

    expect(id).toBe("new-doc-id-abc");
    expect(mockQuery).toHaveBeenCalledOnce();

    const [sql] = mockQuery.mock.calls[0];
    expect(sql).toContain("INSERT INTO documents");
    expect(sql).toContain("RETURNING id");
  });

  it("getDocument() maps snake_case DB row to camelCase Document", async () => {
    const { provider } = await buildProvider({ rows: [DOC_ROW] });

    const doc = await provider.getDocument("uuid-doc-001");

    expect(doc).not.toBeNull();
    expect(doc?.id).toBe("uuid-doc-001");
    expect(doc?.originalName).toBe("Bracket Rev C.pdf");   // snake → camel
    expect(doc?.partNumber).toBe("BRK-001");
    expect(doc?.revision).toBe("C");
    expect(doc?.category).toBe("DRAWING_2D");
    expect(doc?.status).toBe("indexed");
    expect(doc?.storagePath).toBe("DRAWING_2D/bracket-rev-c.pdf");
    expect(doc?.createdAt).toBe("2024-01-01T00:00:00Z");
    expect(doc?.updatedAt).toBe("2024-01-02T00:00:00Z");
  });

  it("getDocument() returns null when document not found", async () => {
    const { provider } = await buildProvider({ rows: [] });

    const doc = await provider.getDocument("nonexistent-id");
    expect(doc).toBeNull();
  });

  it("listDocuments() without filters queries without WHERE clause", async () => {
    const { provider, mockQuery } = await buildProvider({ rows: [DOC_ROW] });

    await provider.listDocuments();

    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).not.toContain("WHERE");
    expect(sql).toContain("ORDER BY created_at DESC");
    expect(params).toHaveLength(0);
  });

  it("listDocuments() with category filter adds WHERE category=$1", async () => {
    const { provider, mockQuery } = await buildProvider({ rows: [] });

    await provider.listDocuments({ category: "DRAWING_2D" });

    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain("WHERE");
    expect(sql).toContain("category");
    expect(params).toContain("DRAWING_2D");
  });

  it("listDocuments() with status filter adds correct condition", async () => {
    const { provider, mockQuery } = await buildProvider({ rows: [] });

    await provider.listDocuments({ status: "indexed" });

    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain("status");
    expect(params).toContain("indexed");
  });

  it("listDocuments() with both filters combines them with AND", async () => {
    const { provider, mockQuery } = await buildProvider({ rows: [] });

    await provider.listDocuments({ category: "INSPECTION_FIR", status: "error" });

    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain("AND");
    expect(params).toContain("INSPECTION_FIR");
    expect(params).toContain("error");
  });

  it("upsertChunk() formats embedding as pgvector string '[x,y,z]'", async () => {
    const { provider, mockQuery } = await buildProvider({ rows: [{ id: "chunk-id-1" }] });

    const embedding = [0.1, 0.2, 0.3];

    await provider.upsertChunk({
      documentId: "doc-001",
      chunkIndex: 0,
      content: "DOCUMENT: bracket.pdf\nCATEGORY: DRAWING_2D\nPART NUMBER: BRK-001",
      contentType: "metadata_header",
      embedding,
      metadata: {},
    });

    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain("INSERT INTO chunks");
    expect(sql).toContain("::vector");  // pgvector cast

    // Embedding must be formatted as [0.1,0.2,0.3]
    const embeddingParam = params.find(
      (p: unknown) => typeof p === "string" && p.startsWith("[") && p.includes(",")
    );
    expect(embeddingParam).toBe("[0.1,0.2,0.3]");
  });

  it("hybridSearch() calls hybrid_search SQL function and maps results", async () => {
    const { provider, mockQuery } = await buildProvider({ rows: [HYBRID_ROW] });

    const embedding = [0.5, 0.25, 0.25];

    const results = await provider.hybridSearch({
      queryEmbedding: embedding,
      queryText: "bore tolerance specification",
      limit: 10,
      filterCategory: "DRAWING_2D",
    });

    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain("hybrid_search");
    expect(params[0]).toBe("[0.5,0.25,0.25]");  // vector string
    expect(params[1]).toBe("bore tolerance specification");
    expect(params[2]).toBe(10);
    expect(params).toContain("DRAWING_2D");

    // Result mapping
    expect(results).toHaveLength(1);
    expect(results[0].chunkId).toBe("chunk-xyz");
    expect(results[0].documentId).toBe("uuid-doc-001");
    expect(results[0].content).toBe("Tolerance: ±0.005 inch on all bores.");
    expect(results[0].rrfScore).toBeCloseTo(0.874);
    expect(results[0].denseRank).toBe(1);
    expect(results[0].sparseRank).toBe(3);
    expect(results[0].pageNumber).toBe(2);
  });

  it("hybridSearch() passes null for filterCategory when not provided", async () => {
    const { provider, mockQuery } = await buildProvider({ rows: [] });

    await provider.hybridSearch({
      queryEmbedding: [0.1],
      queryText: "query",
      limit: 5,
    });

    const [, params] = mockQuery.mock.calls[0];
    // filterCategory and filterPartNumber should be null when not provided
    expect(params).toContain(null);
  });

  it("deleteByDocumentId() runs DELETE on chunks table", async () => {
    const { provider, mockQuery } = await buildProvider({ rows: [] });

    await provider.deleteByDocumentId("doc-to-delete");

    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain("DELETE FROM chunks");
    expect(params).toContain("doc-to-delete");
  });

  it("updateDocumentStatus() updates status and error_message", async () => {
    const { provider, mockQuery } = await buildProvider({ rows: [] });

    await provider.updateDocumentStatus("doc-001", "error", "Embedding model unavailable");

    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain("UPDATE documents");
    expect(params).toContain("error");
    expect(params).toContain("Embedding model unavailable");
    expect(params).toContain("doc-001");
  });

  it("updateDocumentStatus() passes undefined errorMessage as-is", async () => {
    const { provider, mockQuery } = await buildProvider({ rows: [] });

    await provider.updateDocumentStatus("doc-002", "indexed");

    const [, params] = mockQuery.mock.calls[0];
    expect(params).toContain("indexed");
    expect(params).toContain("doc-002");
  });

  it("uses global pg pool singleton to avoid connection leaks", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");

    const mockQuery = vi.fn().mockResolvedValue({ rows: [] });
    let constructorCallCount = 0;

    vi.doMock("pg", () => ({
      Pool: class MockPool {
        constructor() { constructorCallCount++; }
        query = mockQuery;
      },
    }));

    const { createVectorProvider } = await import("@/lib/providers/vector.provider");
    const provider = createVectorProvider();

    // Call multiple methods — each internally calls getPool()
    await provider.getDocument("id-1");
    await provider.getDocument("id-2");
    await provider.listDocuments();

    // Pool should only be instantiated ONCE (singleton via globalThis.__pgPool)
    expect(constructorCallCount).toBe(1);
  });
});
