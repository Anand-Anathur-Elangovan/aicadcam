import { describe, it, expect, afterEach, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

// ─────────────────────────────────────────────────────────────────────────────
// LLM Provider factory
// ─────────────────────────────────────────────────────────────────────────────

describe("createLLMProvider factory", () => {
  it("returns AnthropicLLMProvider for CLOUD mode (default provider)", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "CLOUD");
    vi.stubEnv("CLOUD_LLM_PROVIDER", "anthropic");
    const { createLLMProvider } = await import("@/lib/providers/llm.provider");
    const provider = createLLMProvider();
    expect(provider.constructor.name).toBe("AnthropicLLMProvider");
  });

  it("returns GeminiLLMProvider when CLOUD_LLM_PROVIDER=gemini", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "CLOUD");
    vi.stubEnv("CLOUD_LLM_PROVIDER", "gemini");
    const { createLLMProvider } = await import("@/lib/providers/llm.provider");
    const provider = createLLMProvider();
    expect(provider.constructor.name).toBe("GeminiLLMProvider");
  });

  it("returns LocalLLMProvider for ON_PREM mode", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");
    const { createLLMProvider } = await import("@/lib/providers/llm.provider");
    const provider = createLLMProvider();
    expect(provider.constructor.name).toBe("LocalLLMProvider");
  });

  it("defaults to Anthropic when DEPLOYMENT_MODE is not set", async () => {
    // DEPLOYMENT_MODE defaults to "CLOUD" via ?? operator when undefined
    vi.stubEnv("CLOUD_LLM_PROVIDER", "anthropic");
    const { createLLMProvider } = await import("@/lib/providers/llm.provider");
    const provider = createLLMProvider();
    expect(provider.constructor.name).toBe("AnthropicLLMProvider");
  });

  it("implements LLMProvider interface (streamChat + chat methods)", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "CLOUD");
    const { createLLMProvider } = await import("@/lib/providers/llm.provider");
    const provider = createLLMProvider();
    expect(typeof provider.streamChat).toBe("function");
    expect(typeof provider.chat).toBe("function");
  });

  it("LocalLLMProvider uses correct default Ollama URL", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");
    // LOCAL_LLM_URL is not set → should default to localhost:11434
    const { createLLMProvider } = await import("@/lib/providers/llm.provider");
    const provider = createLLMProvider();
    // Access the private baseUrl through the instance (cast to any for testing)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((provider as any).baseUrl).toBe("http://localhost:11434/v1");
  });

  it("LocalLLMProvider uses env override for URL", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");
    vi.stubEnv("LOCAL_LLM_URL", "http://gpu-server:8080/v1");
    const { createLLMProvider } = await import("@/lib/providers/llm.provider");
    const provider = createLLMProvider();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((provider as any).baseUrl).toBe("http://gpu-server:8080/v1");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Embedding Provider factory
// ─────────────────────────────────────────────────────────────────────────────

describe("createEmbeddingProvider factory", () => {
  it("returns VoyageEmbeddingProvider for CLOUD mode", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "CLOUD");
    const { createEmbeddingProvider } = await import("@/lib/providers/embedding.provider");
    const provider = createEmbeddingProvider();
    expect(provider.constructor.name).toBe("VoyageEmbeddingProvider");
  });

  it("returns LocalOllamaEmbeddingProvider for ON_PREM mode", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");
    const { createEmbeddingProvider } = await import("@/lib/providers/embedding.provider");
    const provider = createEmbeddingProvider();
    expect(provider.constructor.name).toBe("LocalOllamaEmbeddingProvider");
  });

  it("both providers expose dimensions = 1024", async () => {
    for (const mode of ["CLOUD", "ON_PREM"]) {
      vi.stubEnv("DEPLOYMENT_MODE", mode);
      const { createEmbeddingProvider } = await import("@/lib/providers/embedding.provider");
      expect(createEmbeddingProvider().dimensions).toBe(1024);
      vi.resetModules();
    }
  });

  it("both providers implement embed and embedBatch", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "CLOUD");
    const { createEmbeddingProvider } = await import("@/lib/providers/embedding.provider");
    const provider = createEmbeddingProvider();
    expect(typeof provider.embed).toBe("function");
    expect(typeof provider.embedBatch).toBe("function");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Vector Provider factory
// ─────────────────────────────────────────────────────────────────────────────

describe("createVectorProvider factory", () => {
  it("returns SupabaseVectorProvider for CLOUD mode", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "CLOUD");
    const { createVectorProvider } = await import("@/lib/providers/vector.provider");
    const provider = createVectorProvider();
    expect(provider.constructor.name).toBe("SupabaseVectorProvider");
  });

  it("returns LocalPostgresVectorProvider for ON_PREM mode", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");
    const { createVectorProvider } = await import("@/lib/providers/vector.provider");
    const provider = createVectorProvider();
    expect(provider.constructor.name).toBe("LocalPostgresVectorProvider");
  });

  it("both providers implement required VectorProvider interface", async () => {
    const METHODS = [
      "upsertChunk",
      "hybridSearch",
      "deleteByDocumentId",
      "upsertDocument",
      "updateDocumentStatus",
      "getDocument",
      "listDocuments",
    ];

    for (const mode of ["CLOUD", "ON_PREM"]) {
      vi.stubEnv("DEPLOYMENT_MODE", mode);
      const { createVectorProvider } = await import("@/lib/providers/vector.provider");
      const provider = createVectorProvider();
      for (const method of METHODS) {
        expect(
          typeof (provider as Record<string, unknown>)[method],
          `${mode}: ${method} should be a function`
        ).toBe("function");
      }
      vi.resetModules();
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Storage Provider factory
// ─────────────────────────────────────────────────────────────────────────────

describe("createStorageProvider factory", () => {
  it("returns SupabaseStorageProvider for CLOUD mode", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "CLOUD");
    const { createStorageProvider } = await import("@/lib/providers/storage.provider");
    const provider = createStorageProvider();
    expect(provider.constructor.name).toBe("SupabaseStorageProvider");
  });

  it("returns MinioStorageProvider for ON_PREM mode", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");
    const { createStorageProvider } = await import("@/lib/providers/storage.provider");
    const provider = createStorageProvider();
    expect(provider.constructor.name).toBe("MinioStorageProvider");
  });

  it("MinioStorageProvider reads endpoint from env", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");
    vi.stubEnv("MINIO_ENDPOINT", "minio.internal.company.com");
    vi.stubEnv("MINIO_PORT", "9000");
    const { createStorageProvider } = await import("@/lib/providers/storage.provider");
    const provider = createStorageProvider();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((provider as any).endpoint).toBe("minio.internal.company.com");
  });

  it("MinioStorageProvider defaults to localhost when MINIO_ENDPOINT not set", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");
    const { createStorageProvider } = await import("@/lib/providers/storage.provider");
    const provider = createStorageProvider();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((provider as any).endpoint).toBe("localhost");
  });

  it("both providers implement StorageProvider interface", async () => {
    const METHODS = ["upload", "download", "getSignedUrl", "delete", "list"];
    for (const mode of ["CLOUD", "ON_PREM"]) {
      vi.stubEnv("DEPLOYMENT_MODE", mode);
      const { createStorageProvider } = await import("@/lib/providers/storage.provider");
      const provider = createStorageProvider();
      for (const method of METHODS) {
        expect(
          typeof (provider as Record<string, unknown>)[method],
          `${mode}: ${method} should be a function`
        ).toBe("function");
      }
      vi.resetModules();
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Rerank Provider factory
// ─────────────────────────────────────────────────────────────────────────────

describe("createRerankProvider factory", () => {
  it("returns VoyageRerankProvider for CLOUD mode", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "CLOUD");
    const { createRerankProvider } = await import("@/lib/providers/rerank.provider");
    const provider = createRerankProvider();
    expect(provider.constructor.name).toBe("VoyageRerankProvider");
  });

  it("returns LocalRerankProvider for ON_PREM mode", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");
    const { createRerankProvider } = await import("@/lib/providers/rerank.provider");
    const provider = createRerankProvider();
    expect(provider.constructor.name).toBe("LocalRerankProvider");
  });

  it("both providers implement rerank method", async () => {
    for (const mode of ["CLOUD", "ON_PREM"]) {
      vi.stubEnv("DEPLOYMENT_MODE", mode);
      const { createRerankProvider } = await import("@/lib/providers/rerank.provider");
      expect(typeof createRerankProvider().rerank).toBe("function");
      vi.resetModules();
    }
  });
});
