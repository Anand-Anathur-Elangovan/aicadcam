import { describe, it, expect, vi, afterEach } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
  vi.unstubAllGlobals();
});

const MOCK_EMBEDDING = new Array(1024).fill(0).map((_, i) => i * 0.001);

// ─────────────────────────────────────────────────────────────────────────────
// VoyageEmbeddingProvider (CLOUD)
// ─────────────────────────────────────────────────────────────────────────────

describe("VoyageEmbeddingProvider", () => {
  it("embed() calls embedBatch and returns first result", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "CLOUD");
    vi.stubEnv("VOYAGE_API_KEY", "voyage-test-key");

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            data: [{ embedding: MOCK_EMBEDDING }],
          }),
      })
    );

    const { createEmbeddingProvider } = await import("@/lib/providers/embedding.provider");
    const provider = createEmbeddingProvider();
    const result = await provider.embed("tolerance specification");

    expect(result).toHaveLength(1024);
    expect(result[0]).toBeCloseTo(0);
    expect(result[1]).toBeCloseTo(0.001);
  });

  it("embedBatch() returns an embedding per input text", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "CLOUD");
    vi.stubEnv("VOYAGE_API_KEY", "voyage-test-key");

    const texts = ["first document", "second document", "third document"];

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            data: texts.map((_, i) => ({ embedding: new Array(1024).fill(i * 0.1) })),
          }),
      })
    );

    const { createEmbeddingProvider } = await import("@/lib/providers/embedding.provider");
    const result = await createEmbeddingProvider().embedBatch(texts);

    expect(result).toHaveLength(3);
    expect(result[0]).toHaveLength(1024);
    expect(result[1][0]).toBeCloseTo(0.1);
    expect(result[2][0]).toBeCloseTo(0.2);
  });

  it("embedBatch() sends correct payload to Voyage API", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "CLOUD");
    vi.stubEnv("VOYAGE_API_KEY", "test-key-xyz");
    vi.stubEnv("VOYAGE_EMBEDDING_MODEL", "voyage-multimodal-3");

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ data: [{ embedding: MOCK_EMBEDDING }] }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const { createEmbeddingProvider } = await import("@/lib/providers/embedding.provider");
    await createEmbeddingProvider().embedBatch(["test text"]);

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe("https://api.voyageai.com/v1/embeddings");
    expect(init.headers.Authorization).toBe("Bearer test-key-xyz");

    const body = JSON.parse(init.body);
    expect(body.input).toEqual(["test text"]);
    expect(body.model).toBe("voyage-multimodal-3");
  });

  it("throws with clear message when Voyage API returns non-OK", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "CLOUD");
    vi.stubEnv("VOYAGE_API_KEY", "bad-key");

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        text: () => Promise.resolve("403 Forbidden — invalid API key"),
      })
    );

    const { createEmbeddingProvider } = await import("@/lib/providers/embedding.provider");
    await expect(createEmbeddingProvider().embed("text")).rejects.toThrow(
      "Voyage embedding failed"
    );
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// LocalOllamaEmbeddingProvider (ON_PREM)
// ─────────────────────────────────────────────────────────────────────────────

describe("LocalOllamaEmbeddingProvider", () => {
  it("embed() sends text to local Ollama embedding endpoint", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");
    vi.stubEnv("LOCAL_EMBEDDING_URL", "http://localhost:11434/api/embeddings");
    vi.stubEnv("LOCAL_EMBEDDING_MODEL", "bge-m3");

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ embedding: MOCK_EMBEDDING }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const { createEmbeddingProvider } = await import("@/lib/providers/embedding.provider");
    const result = await createEmbeddingProvider().embed("machining specification");

    expect(result).toHaveLength(1024);

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe("http://localhost:11434/api/embeddings");
    const body = JSON.parse(init.body);
    expect(body.model).toBe("bge-m3");
    expect(body.prompt).toBe("machining specification");
  });

  it("embedBatch() processes texts in batches of 5 (concurrency cap)", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");

    let callCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => {
        callCount++;
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ embedding: MOCK_EMBEDDING }),
        });
      })
    );

    const { createEmbeddingProvider } = await import("@/lib/providers/embedding.provider");
    const texts = Array.from({ length: 11 }, (_, i) => `document ${i}`);
    const result = await createEmbeddingProvider().embedBatch(texts);

    // 11 texts → 11 individual embed() calls (no native batch in Ollama)
    expect(callCount).toBe(11);
    expect(result).toHaveLength(11);
    // Each embedding is the mock 1024-dim vector
    for (const emb of result) {
      expect(emb).toHaveLength(1024);
    }
  });

  it("embedBatch() with exactly 5 texts (single batch) calls fetch 5 times", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");

    const callCount = { value: 0 };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => {
        callCount.value++;
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ embedding: MOCK_EMBEDDING }),
        });
      })
    );

    const { createEmbeddingProvider } = await import("@/lib/providers/embedding.provider");
    await createEmbeddingProvider().embedBatch(["a", "b", "c", "d", "e"]);
    expect(callCount.value).toBe(5);
  });

  it("throws when local embedding endpoint returns error", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        text: () => Promise.resolve("model not found"),
      })
    );

    const { createEmbeddingProvider } = await import("@/lib/providers/embedding.provider");
    await expect(createEmbeddingProvider().embed("text")).rejects.toThrow(
      "Local embedding failed"
    );
  });
});
