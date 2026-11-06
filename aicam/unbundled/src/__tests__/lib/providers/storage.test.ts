import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

// ─────────────────────────────────────────────────────────────────────────────
// Helpers: shared mutable method stubs that class instances delegate to.
// Using a mutable object avoids the vi.mock hoisting/closure issue.
// ─────────────────────────────────────────────────────────────────────────────

let minioStubs = {
  bucketExists: vi.fn().mockResolvedValue(true),
  makeBucket: vi.fn().mockResolvedValue(undefined),
  putObject: vi.fn().mockResolvedValue(undefined),
  getObject: vi.fn().mockResolvedValue(null),
  presignedGetObject: vi.fn().mockResolvedValue("http://minio/signed"),
  removeObject: vi.fn().mockResolvedValue(undefined),
  listObjects: vi.fn().mockReturnValue(null),
};

beforeEach(() => {
  // Reset all stubs to defaults before each test
  minioStubs.bucketExists.mockResolvedValue(true);
  minioStubs.makeBucket.mockResolvedValue(undefined);
  minioStubs.putObject.mockResolvedValue(undefined);
  minioStubs.presignedGetObject.mockResolvedValue("http://minio/signed");
  minioStubs.removeObject.mockResolvedValue(undefined);
  vi.clearAllMocks();
  // Re-assign to ensure vi.fn() calls reset
  minioStubs = {
    bucketExists: vi.fn().mockResolvedValue(true),
    makeBucket: vi.fn().mockResolvedValue(undefined),
    putObject: vi.fn().mockResolvedValue(undefined),
    getObject: vi.fn().mockResolvedValue(null),
    presignedGetObject: vi.fn().mockResolvedValue("http://minio/signed"),
    removeObject: vi.fn().mockResolvedValue(undefined),
    listObjects: vi.fn().mockReturnValue(null),
  };
});

// ─────────────────────────────────────────────────────────────────────────────
// MinIO Provider — key behavior tests using class-based mocks
// ─────────────────────────────────────────────────────────────────────────────

describe("MinioStorageProvider", () => {
  async function buildProvider(
    env: Record<string, string> = {},
    stubOverrides: Partial<typeof minioStubs> = {}
  ) {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");
    vi.stubEnv("MINIO_ENDPOINT", env.endpoint ?? "localhost");
    vi.stubEnv("MINIO_PORT", env.port ?? "9000");
    if (env.useSSL) vi.stubEnv("MINIO_USE_SSL", env.useSSL);

    const stubs = { ...minioStubs, ...stubOverrides };

    vi.doMock("minio", () => ({
      // Must use class (not arrow fn) to be usable with `new`
      Client: class MockMinioClient {
        bucketExists = stubs.bucketExists;
        makeBucket = stubs.makeBucket;
        putObject = stubs.putObject;
        getObject = stubs.getObject;
        presignedGetObject = stubs.presignedGetObject;
        removeObject = stubs.removeObject;
        listObjects = stubs.listObjects;
      },
    }));

    const { createStorageProvider } = await import("@/lib/providers/storage.provider");
    return { provider: createStorageProvider(), stubs };
  }

  it("upload() constructs http URL when useSSL=false", async () => {
    const { provider } = await buildProvider({
      endpoint: "minio.local",
      port: "9000",
      useSSL: "false",
    });

    const result = await provider.upload({
      bucket: "manufacturing-docs",
      path: "DRAWING_2D/part.pdf",
      data: Buffer.from("pdf"),
      contentType: "application/pdf",
    });

    expect(result.path).toBe("DRAWING_2D/part.pdf");
    expect(result.url).toBe("http://minio.local:9000/manufacturing-docs/DRAWING_2D/part.pdf");
  });

  it("upload() constructs https URL when useSSL=true [bug fix verified]", async () => {
    const { provider } = await buildProvider({
      endpoint: "secure-minio.corp.com",
      port: "9443",
      useSSL: "true",
    });

    const result = await provider.upload({
      bucket: "manufacturing-docs",
      path: "DRAWING_2D/secure.pdf",
      data: Buffer.from("content"),
      contentType: "application/pdf",
    });

    // Bug was: always used 'http://' even when useSSL=true
    expect(result.url).toBe(
      "https://secure-minio.corp.com:9443/manufacturing-docs/DRAWING_2D/secure.pdf"
    );
  });

  it("upload() creates bucket when it does not exist", async () => {
    const makeBucket = vi.fn().mockResolvedValue(undefined);
    const { stubs } = await buildProvider({}, {
      bucketExists: vi.fn().mockResolvedValue(false),
      makeBucket,
    });

    const { createStorageProvider } = await import("@/lib/providers/storage.provider");
    await createStorageProvider().upload({
      bucket: "new-bucket",
      path: "file.pdf",
      data: Buffer.from("x"),
      contentType: "application/pdf",
    });

    expect(stubs.makeBucket).toHaveBeenCalledWith("new-bucket");
  });

  it("upload() skips bucket creation when bucket already exists", async () => {
    const { stubs } = await buildProvider({}, {
      bucketExists: vi.fn().mockResolvedValue(true),
    });

    const { createStorageProvider } = await import("@/lib/providers/storage.provider");
    await createStorageProvider().upload({
      bucket: "existing",
      path: "file.pdf",
      data: Buffer.from("x"),
      contentType: "application/pdf",
    });

    expect(stubs.makeBucket).not.toHaveBeenCalled();
  });

  it("download() reads buffer from MinIO stream", async () => {
    // Stream events must fire AFTER download() adds its listeners.
    // Use setImmediate inside the mock so events fire after await resolves.
    const { provider } = await buildProvider({}, {
      getObject: vi.fn().mockImplementation(() => {
        const { Readable } = require("stream");
        const stream = new Readable({ read() {} });
        setImmediate(() => {
          stream.emit("data", Buffer.from("part-drawing-bytes"));
          stream.emit("end");
        });
        return Promise.resolve(stream);
      }),
    });

    const buf = await provider.download("manufacturing-docs", "DRAWING_2D/part.pdf");
    expect(buf).toBeInstanceOf(Buffer);
    expect(buf.toString()).toBe("part-drawing-bytes");
  });

  it("getSignedUrl() delegates to presignedGetObject with correct args", async () => {
    const presignedGetObject = vi.fn().mockResolvedValue("http://minio/signed?token=xyz");
    const { provider } = await buildProvider({}, { presignedGetObject });

    const url = await provider.getSignedUrl("docs", "file.pdf", 3600);
    expect(url).toBe("http://minio/signed?token=xyz");
    expect(presignedGetObject).toHaveBeenCalledWith("docs", "file.pdf", 3600);
  });

  it("delete() calls removeObject with correct bucket and path", async () => {
    const removeObject = vi.fn().mockResolvedValue(undefined);
    const { provider } = await buildProvider({}, { removeObject });

    await provider.delete("manufacturing-docs", "old-part.pdf");
    expect(removeObject).toHaveBeenCalledWith("manufacturing-docs", "old-part.pdf");
  });

  it("list() collects file objects from MinIO object stream", async () => {
    const { provider } = await buildProvider({}, {
      listObjects: vi.fn().mockImplementation(() => {
        const { Readable } = require("stream");
        const objectStream = new Readable({ objectMode: true, read() {} });
        setImmediate(() => {
          objectStream.emit("data", { name: "DRAWING_2D/part-a.pdf", size: 5000 });
          objectStream.emit("data", { name: "DRAWING_2D/part-b.pdf", size: 8200 });
          objectStream.emit("end");
        });
        return objectStream;
      }),
    });

    const files = await provider.list("manufacturing-docs", "DRAWING_2D/");
    expect(files).toHaveLength(2);
    expect(files[0]).toEqual({ name: "DRAWING_2D/part-a.pdf", size: 5000 });
    expect(files[1]).toEqual({ name: "DRAWING_2D/part-b.pdf", size: 8200 });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// MinioStorageProvider — config validation
// ─────────────────────────────────────────────────────────────────────────────

describe("MinioStorageProvider configuration", () => {
  it("reads endpoint from MINIO_ENDPOINT env var", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");
    vi.stubEnv("MINIO_ENDPOINT", "minio.internal.corp.com");
    const { createStorageProvider } = await import("@/lib/providers/storage.provider");
    const provider = createStorageProvider();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((provider as any).endpoint).toBe("minio.internal.corp.com");
  });

  it("defaults to localhost when MINIO_ENDPOINT not set", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");
    const { createStorageProvider } = await import("@/lib/providers/storage.provider");
    const provider = createStorageProvider();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((provider as any).endpoint).toBe("localhost");
  });

  it("parses MINIO_PORT as integer", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");
    vi.stubEnv("MINIO_PORT", "9001");
    const { createStorageProvider } = await import("@/lib/providers/storage.provider");
    const provider = createStorageProvider();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((provider as any).port).toBe(9001);
  });

  it("useSSL is false by default", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");
    const { createStorageProvider } = await import("@/lib/providers/storage.provider");
    const provider = createStorageProvider();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((provider as any).useSSL).toBe(false);
  });

  it("useSSL is true when MINIO_USE_SSL=true", async () => {
    vi.stubEnv("DEPLOYMENT_MODE", "ON_PREM");
    vi.stubEnv("MINIO_USE_SSL", "true");
    const { createStorageProvider } = await import("@/lib/providers/storage.provider");
    const provider = createStorageProvider();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((provider as any).useSSL).toBe(true);
  });
});
