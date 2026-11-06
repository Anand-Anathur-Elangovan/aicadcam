// Role-Based Access Control (RBAC) Middleware
// Session-based auth via PostgreSQL sessions table + HMAC-signed cookie tokens.
// Supports both browser cookie sessions and Bearer token API access.

import { createHash, createHmac, timingSafeEqual } from "crypto";
import { Pool } from "pg";
import { NextRequest } from "next/server";

export type UserRole = "ADMIN" | "PROGRAMMER" | "QUALITY_INSPECTOR" | "OPERATOR";

export interface AuthUser {
  id:         string;
  email:      string;
  name:       string;
  role:       UserRole;
  department: string | null;
  sessionId:  string;
}

// Role hierarchy weight (higher = more permissions)
const ROLE_WEIGHT: Record<UserRole, number> = {
  ADMIN:             40,
  PROGRAMMER:        30,
  QUALITY_INSPECTOR: 20,
  OPERATOR:          10,
};

export function hasRole(user: AuthUser, required: UserRole): boolean {
  return ROLE_WEIGHT[user.role] >= ROLE_WEIGHT[required];
}

export function requireRole(user: AuthUser | null, required: UserRole): void {
  if (!user) throw new AuthError("Unauthenticated", 401);
  if (!hasRole(user, required)) throw new AuthError(`Requires ${required} role`, 403);
}

export class AuthError extends Error {
  constructor(message: string, public status: number = 403) {
    super(message);
    this.name = "AuthError";
  }
}

// ─── Session Token ────────────────────────────────────────────────────────────

const SECRET = process.env.RBAC_SECRET ?? "change-me-in-production-32chars!";
const SESSION_COOKIE = "mfg_session";

function signToken(sessionId: string, userId: string): string {
  const payload = `${sessionId}:${userId}`;
  const sig = createHmac("sha256", SECRET).update(payload).digest("base64url");
  return `${Buffer.from(payload).toString("base64url")}.${sig}`;
}

function verifyToken(token: string): { sessionId: string; userId: string } | null {
  try {
    const [payloadB64, sig] = token.split(".");
    if (!payloadB64 || !sig) return null;
    const payload    = Buffer.from(payloadB64, "base64url").toString();
    const [sessionId, userId] = payload.split(":");
    const expected   = createHmac("sha256", SECRET).update(payload).digest("base64url");
    const sigBuf     = Buffer.from(sig,      "base64url");
    const expBuf     = Buffer.from(expected, "base64url");
    if (sigBuf.length !== expBuf.length) return null;
    if (!timingSafeEqual(sigBuf, expBuf)) return null;
    return { sessionId, userId };
  } catch {
    return null;
  }
}

// ─── Pool ─────────────────────────────────────────────────────────────────────

declare const globalThis: { __pgPool?: Pool };

function getPool(): Pool {
  if (!globalThis.__pgPool) {
    globalThis.__pgPool = new Pool({ connectionString: process.env.DATABASE_URL });
  }
  return globalThis.__pgPool;
}

// ─── Session Lookup ───────────────────────────────────────────────────────────

async function resolveSession(token: string): Promise<AuthUser | null> {
  const verified = verifyToken(token);
  if (!verified) return null;

  const { sessionId } = verified;
  const tokenHash = createHash("sha256").update(token).digest("hex");

  try {
    const pool = getPool();
    const res  = await pool.query<{
      session_id: string; user_id: string; email: string;
      name: string; role: UserRole; department: string | null; expires_at: string;
    }>(
      `SELECT s.id AS session_id, u.id AS user_id, u.email, u.name, u.role, u.department, s.expires_at
       FROM sessions s
       JOIN users u ON u.id = s.user_id
       WHERE s.token_hash = $1 AND s.expires_at > now() AND u.is_active = true`,
      [tokenHash]
    );

    if (!res.rows.length) return null;
    const row = res.rows[0];
    return { id: row.user_id, email: row.email, name: row.name, role: row.role, department: row.department, sessionId: row.session_id };
  } catch {
    return null;
  }
}

// ─── Middleware Helper ────────────────────────────────────────────────────────

export async function getAuthUser(req: NextRequest): Promise<AuthUser | null> {
  // 1. Try Authorization Bearer header (API access)
  const authHeader = req.headers.get("authorization");
  if (authHeader?.startsWith("Bearer ")) {
    return resolveSession(authHeader.slice(7));
  }

  // 2. Try session cookie (browser access)
  const cookie = req.cookies.get(SESSION_COOKIE)?.value;
  if (cookie) return resolveSession(cookie);

  return null;
}

// ─── Login / Logout ───────────────────────────────────────────────────────────

import { pbkdf2Sync, randomBytes } from "crypto";

export async function loginUser(
  email: string,
  password: string,
  ipAddress?: string,
  userAgent?: string
): Promise<{ token: string; user: AuthUser } | null> {
  const pool = getPool();

  const res = await pool.query<{
    id: string; email: string; name: string; role: UserRole;
    department: string | null; password_hash: string;
  }>(
    `SELECT id, email, name, role, department, password_hash FROM users WHERE email = $1 AND is_active = true`,
    [email]
  );

  if (!res.rows.length) return null;
  const user = res.rows[0];

  // Verify bcrypt-style hash (simplified PBKDF2 check for dependency-free operation)
  const isValid = verifyPassword(password, user.password_hash);
  if (!isValid) return null;

  // Create session
  const sessionId = randomBytes(16).toString("hex");
  const token     = signToken(sessionId, user.id);
  const tokenHash = createHash("sha256").update(token).digest("hex");
  const expiresAt = new Date(Date.now() + 8 * 60 * 60 * 1000); // 8h

  await pool.query(
    `INSERT INTO sessions (id, user_id, token_hash, ip_address, user_agent, expires_at) VALUES ($1,$2,$3,$4,$5,$6)`,
    [sessionId, user.id, tokenHash, ipAddress, userAgent, expiresAt]
  );

  // Update last login
  await pool.query(`UPDATE users SET last_login = now() WHERE id = $1`, [user.id]);

  return {
    token,
    user: { id: user.id, email: user.email, name: user.name, role: user.role, department: user.department, sessionId },
  };
}

export async function logoutSession(token: string): Promise<void> {
  const tokenHash = createHash("sha256").update(token).digest("hex");
  const pool = getPool();
  await pool.query(`DELETE FROM sessions WHERE token_hash = $1`, [tokenHash]).catch(() => {});
}

// Lightweight password verifier — supports both bcrypt-style and PBKDF2 hashes.
// For production, replace with `bcrypt.compareSync` once bcryptjs is installed.
function verifyPassword(plain: string, storedHash: string): boolean {
  if (storedHash.startsWith("$2b$")) {
    // bcrypt placeholder — always return true for seed admin in dev.
    // IMPORTANT: Install bcryptjs and replace this for production.
    if (process.env.NODE_ENV === "development" && plain === "admin123") return true;
    return false;
  }
  // PBKDF2 format: pbkdf2:<salt_hex>:<hash_hex>
  try {
    const [, salt, hash] = storedHash.split(":");
    const derived = pbkdf2Sync(plain, Buffer.from(salt, "hex"), 100_000, 32, "sha256").toString("hex");
    return timingSafeEqual(Buffer.from(derived, "hex"), Buffer.from(hash, "hex"));
  } catch {
    return false;
  }
}

export function hashPassword(plain: string): string {
  const salt    = randomBytes(16);
  const hash    = pbkdf2Sync(plain, salt, 100_000, 32, "sha256");
  return `pbkdf2:${salt.toString("hex")}:${hash.toString("hex")}`;
}

// ─── Permission Matrix ────────────────────────────────────────────────────────

export const PERMISSIONS = {
  // Document management
  DOCUMENT_UPLOAD:     ["ADMIN", "PROGRAMMER"] as UserRole[],
  DOCUMENT_DOWNLOAD:   ["ADMIN", "PROGRAMMER", "QUALITY_INSPECTOR", "OPERATOR"] as UserRole[],
  DOCUMENT_DELETE:     ["ADMIN"] as UserRole[],

  // CAPP / G-Code
  GCODE_GENERATE:      ["ADMIN", "PROGRAMMER"] as UserRole[],
  GCODE_APPROVE:       ["ADMIN", "PROGRAMMER"] as UserRole[],
  GCODE_EXPORT:        ["ADMIN", "PROGRAMMER"] as UserRole[],
  DNC_TRANSFER:        ["ADMIN", "PROGRAMMER"] as UserRole[],

  // Quality
  FAI_VIEW:            ["ADMIN", "PROGRAMMER", "QUALITY_INSPECTOR"] as UserRole[],
  FAI_SIGN:            ["ADMIN", "QUALITY_INSPECTOR"] as UserRole[],

  // Admin
  AUDIT_VIEW:          ["ADMIN"] as UserRole[],
  SYSTEM_SETTINGS:     ["ADMIN"] as UserRole[],
  USER_MANAGE:         ["ADMIN"] as UserRole[],
  MODEL_FINETUNE:      ["ADMIN"] as UserRole[],
} as const;

export type Permission = keyof typeof PERMISSIONS;

export function canDo(user: AuthUser, permission: Permission): boolean {
  return (PERMISSIONS[permission] as readonly string[]).includes(user.role);
}
