import type { StorageProvider } from "../types";

// ─── Supabase Storage Provider ────────────────────────────────────────────────
class SupabaseStorageProvider implements StorageProvider {
  private get client() {
    const { createClient } = require("@supabase/supabase-js");
    return createClient(process.env.SUPABASE_URL!, process.env.SUPABASE_SERVICE_ROLE_KEY!);
  }

  async upload({ bucket, path, data, contentType }: Parameters<StorageProvider["upload"]>[0]) {
    const { data: result, error } = await this.client.storage
      .from(bucket)
      .upload(path, data, { contentType, upsert: true });
    if (error) throw new Error(`Supabase upload: ${error.message}`);
    const { data: urlData } = this.client.storage.from(bucket).getPublicUrl(result.path);
    return { path: result.path, url: urlData.publicUrl };
  }

  async download(bucket: string, path: string): Promise<Buffer> {
    const { data, error } = await this.client.storage.from(bucket).download(path);
    if (error) throw new Error(`Supabase download: ${error.message}`);
    return Buffer.from(await data.arrayBuffer());
  }

  async getSignedUrl(bucket: string, path: string, expiresIn = 3600): Promise<string> {
    const { data, error } = await this.client.storage.from(bucket).createSignedUrl(path, expiresIn);
    if (error) throw new Error(`Supabase signedUrl: ${error.message}`);
    return data.signedUrl;
  }

  async delete(bucket: string, path: string): Promise<void> {
    const { error } = await this.client.storage.from(bucket).remove([path]);
    if (error) throw new Error(`Supabase delete: ${error.message}`);
  }

  async list(bucket: string, prefix = ""): Promise<{ name: string; size: number }[]> {
    const { data, error } = await this.client.storage.from(bucket).list(prefix);
    if (error) throw new Error(`Supabase list: ${error.message}`);
    return (data ?? []).map((f: { name: string; metadata?: { size?: number } }) => ({
      name: f.name,
      size: f.metadata?.size ?? 0,
    }));
  }
}

// ─── MinIO / S3-Compatible Storage Provider ───────────────────────────────────
class MinioStorageProvider implements StorageProvider {
  private endpoint: string;
  private port: number;
  private useSSL: boolean;
  private accessKey: string;
  private secretKey: string;

  constructor() {
    this.endpoint = process.env.MINIO_ENDPOINT ?? "localhost";
    this.port = parseInt(process.env.MINIO_PORT ?? "9000");
    this.useSSL = process.env.MINIO_USE_SSL === "true";
    this.accessKey = process.env.MINIO_ROOT_USER ?? "minioadmin";
    this.secretKey = process.env.MINIO_ROOT_PASSWORD ?? "minioadmin";
  }

  private async getClient() {
    const { Client } = await import("minio");
    return new Client({
      endPoint: this.endpoint,
      port: this.port,
      useSSL: this.useSSL,
      accessKey: this.accessKey,
      secretKey: this.secretKey,
    });
  }

  async upload({ bucket, path, data, contentType }: Parameters<StorageProvider["upload"]>[0]) {
    const client = await this.getClient();
    // Ensure bucket exists
    const exists = await client.bucketExists(bucket);
    if (!exists) await client.makeBucket(bucket);

    await client.putObject(bucket, path, data, data.length, { "Content-Type": contentType });
    const protocol = this.useSSL ? "https" : "http";
    const url = `${protocol}://${this.endpoint}:${this.port}/${bucket}/${path}`;
    return { path, url };
  }

  async download(bucket: string, path: string): Promise<Buffer> {
    const client = await this.getClient();
    const stream = await client.getObject(bucket, path);
    const chunks: Buffer[] = [];
    return new Promise((resolve, reject) => {
      stream.on("data", (chunk: Buffer) => chunks.push(chunk));
      stream.on("end", () => resolve(Buffer.concat(chunks)));
      stream.on("error", reject);
    });
  }

  async getSignedUrl(bucket: string, path: string, expiresIn = 3600): Promise<string> {
    const client = await this.getClient();
    return client.presignedGetObject(bucket, path, expiresIn);
  }

  async delete(bucket: string, path: string): Promise<void> {
    const client = await this.getClient();
    await client.removeObject(bucket, path);
  }

  async list(bucket: string, prefix = ""): Promise<{ name: string; size: number }[]> {
    const client = await this.getClient();
    const objects: { name: string; size: number }[] = [];
    const stream = client.listObjects(bucket, prefix, true);
    return new Promise((resolve, reject) => {
      stream.on("data", (obj: { name?: string; size?: number }) => {
        if (obj.name) objects.push({ name: obj.name, size: obj.size ?? 0 });
      });
      stream.on("end", () => resolve(objects));
      stream.on("error", reject);
    });
  }
}

// ─── Factory ─────────────────────────────────────────────────────────────────
export function createStorageProvider(): StorageProvider {
  const mode = process.env.DEPLOYMENT_MODE ?? "CLOUD";
  return mode === "ON_PREM" ? new MinioStorageProvider() : new SupabaseStorageProvider();
}
