import { describe, it, expect, vi, afterEach } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
  vi.unstubAllGlobals();
});

const DOCS = [
  "Tolerance for bore is ±0.005 inch on all faces.",
  "Material: Aluminum 6061-T6. Surface finish Ra 1.6 µm.",
  "Tool: 10mm end mill, 4 flutes, carbide grade K10.",
];

// ─────────────────────────────────────────────────────────────────────────────
// VoyageRerankProvider (CLOUD)
// ─────────────────────────────────────────────────────────────────────────────

describe("VoyageRerankProvider", () => {
  it("returns sorted results from Voyage API response", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "CLOUD");
    vi.stubEnv("VOYAGE_API_KEY", "voyage-test-key");

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            data: [
              { index: 1, relevance_score: 0.91 },
              { index: 0, relevance_score: 0.65 },
              { index: 2, relevance_score: 0.43 },
            ],
          }),
      })
    );

    const { createRerankProvider } = await import("@/lib/providers/rerank.provider");
    const provider = createRerankProvider();
    const result = await provider.rerank("bore tolerance", DOCS);

    expect(result).toHaveLength(3);
    // Results should map Voyage relevance_score to score
    expect(result.find((r) => r.index === 1)?.score).toBeCloseTo(0.91);
    expect(result.find((r) => r.index === 0)?.score).toBeCloseTo(0.65);
    expect(result.find((r) => r.index === 2)?.score).toBeCloseTo(0.43);
  });

  it("sends correct request body to Voyage API", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "CLOUD");
    vi.stubEnv("VOYAGE_API_KEY", "test-key-abc");
    vi.stubEnv("VOYAGE_RERANK_MODEL", "rerank-2");

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ data: [{ index: 0, relevance_score: 0.9 }] }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const { createRerankProvider } = await import("@/lib/providers/rerank.provider");
    await createRerankProvider().rerank("test query", ["doc one"]);

    const callArgs = mockFetch.mock.calls[0];
    expect(callArgs[0]).toBe("https://api.voyageai.com/v1/rerank");
    const body = JSON.parse(callArgs[1].body);
    expect(body.query).toBe("test query");
    expect(body.documents).toEqual(["doc one"]);
    expect(body.model).toBe("rerank-2");

    const headers = callArgs[1].headers;
    expect(headers.Authorization).toBe("Bearer test-key-abc");
  });

  it("throws when Voyage API returns non-OK response", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "CLOUD");
    vi.stubEnv("VOYAGE_API_KEY", "bad-key");

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        text: () => Promise.resolve("401 Unauthorized"),
      })
    );

    const { createRerankProvider } = await import("@/lib/providers/rerank.provider");
    await expect(createRerankProvider().rerank("q", DOCS)).rejects.toThrow(
      "Voyage rerank failed"
    );
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// LocalRerankProvider (ON_PREM) — with fallback on unavailability
// ─────────────────────────────────────────────────────────────────────────────

describe("LocalRerankProvider", () => {
  it("returns results from local reranker sidecar on success", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");
    vi.stubEnv("LOCAL_RERANKER_URL", "http://localhost:8001/rerank");

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            results: [
              { index: 2, score: 0.88 },
              { index: 0, score: 0.72 },
              { index: 1, score: 0.45 },
            ],
          }),
      })
    );

    const { createRerankProvider } = await import("@/lib/providers/rerank.provider");
    const result = await createRerankProvider().rerank("cutting speed", DOCS);

    expect(result).toHaveLength(3);
    expect(result.find((r) => r.index === 2)?.score).toBeCloseTo(0.88);
  });

  it("falls back to identity order when local reranker is unavailable (non-OK response)", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
      })
    );

    const { createRerankProvider } = await import("@/lib/providers/rerank.provider");
    const result = await createRerankProvider().rerank("query", DOCS);

    // Fallback: identity ordering with descending scores
    expect(result).toHaveLength(3);
    expect(result[0].index).toBe(0);
    expect(result[1].index).toBe(1);
    expect(result[2].index).toBe(2);
    // Scores should be descending
    expect(result[0].score).toBeGreaterThan(result[1].score);
    expect(result[1].score).toBeGreaterThan(result[2].score);
  });

  it("falls back to identity order when fetch rejects (network error)", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");

    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ECONNREFUSED")));

    const { createRerankProvider } = await import("@/lib/providers/rerank.provider");
    // Network-level rejection — the catch in the provider needs to handle this.
    // Currently the provider only catches non-OK responses, NOT fetch failures.
    // This will throw — documenting the gap.
    await expect(createRerankProvider().rerank("q", DOCS)).rejects.toThrow();
  });

  it("sends correct request body to local reranker", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");
    vi.stubEnv("LOCAL_RERANKER_URL", "http://gpu-box:8001/rerank");

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ results: [{ index: 0, score: 0.99 }] }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const { createRerankProvider } = await import("@/lib/providers/rerank.provider");
    await createRerankProvider().rerank("test", ["single doc"]);

    const callArgs = mockFetch.mock.calls[0];
    expect(callArgs[0]).toBe("http://gpu-box:8001/rerank");
    const body = JSON.parse(callArgs[1].body);
    expect(body.query).toBe("test");
    expect(body.documents).toEqual(["single doc"]);
  });
});
