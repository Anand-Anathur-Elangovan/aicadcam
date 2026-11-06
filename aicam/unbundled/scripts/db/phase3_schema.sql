-- Phase 3 Schema: Feedback Engine, RBAC, Audit Logging, DNC Machine Registry
-- Run after init.sql: psql -U postgres -d manufacturing_ai -f phase3_schema.sql

-- ─── Users & Sessions ────────────────────────────────────────────────────────

CREATE TYPE user_role AS ENUM ('ADMIN', 'PROGRAMMER', 'QUALITY_INSPECTOR', 'OPERATOR');

CREATE TABLE IF NOT EXISTS users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email         TEXT UNIQUE NOT NULL,
  name          TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role          user_role NOT NULL DEFAULT 'OPERATOR',
  department    TEXT,
  is_active     BOOLEAN NOT NULL DEFAULT true,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_login    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS sessions (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  ip_address TEXT,
  user_agent TEXT,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sessions_token   ON sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);

-- ─── Immutable Audit Log ─────────────────────────────────────────────────────

CREATE TYPE audit_action AS ENUM (
  'LOGIN', 'LOGOUT',
  'DOCUMENT_VIEW', 'DOCUMENT_DOWNLOAD', 'DOCUMENT_UPLOAD', 'DOCUMENT_DELETE',
  'GCODE_GENERATE', 'GCODE_EDIT', 'GCODE_APPROVE', 'GCODE_EXPORT',
  'PROCESS_PLAN_GENERATE', 'PROCESS_PLAN_APPROVE',
  'DNC_TRANSFER',
  'FAI_VIEW', 'FAI_SIGN',
  'SYSTEM_SETTINGS_CHANGE', 'USER_MANAGE',
  'MODEL_FINETUNE_START', 'MODEL_FINETUNE_COMPLETE'
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id            BIGSERIAL PRIMARY KEY,
  timestamp     TIMESTAMPTZ NOT NULL DEFAULT now(),
  user_id       UUID REFERENCES users(id),
  user_email    TEXT,
  user_role     TEXT,
  ip_address    TEXT,
  user_agent    TEXT,
  action        audit_action NOT NULL,
  resource_type TEXT,
  resource_id   TEXT,
  resource_name TEXT,
  details       JSONB DEFAULT '{}',
  checksum      TEXT NOT NULL,   -- SHA256(timestamp||user_id||action||resource_id)
  session_id    UUID REFERENCES sessions(id)
);

-- Audit log is append-only: no UPDATE or DELETE allowed (enforced via policy)
CREATE INDEX IF NOT EXISTS idx_audit_timestamp   ON audit_logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user_id     ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_action      ON audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_audit_resource_id ON audit_logs(resource_id);

-- ─── G-Code Diff Snapshots ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS gcode_diffs (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  approved_at           TIMESTAMPTZ,
  approved_by_user_id   UUID REFERENCES users(id),
  approved_by_email     TEXT,

  -- Source context
  part_number           TEXT,
  part_name             TEXT,
  revision              TEXT,
  material              TEXT,
  machine_type          TEXT,
  part_geometry_hash    TEXT,   -- SHA256 of feature JSON for retrieval

  -- The AI-generated baseline
  generated_gcode       TEXT NOT NULL,
  generated_plan_json   JSONB,

  -- The human-approved version
  approved_gcode        TEXT,
  edit_reason           TEXT,   -- engineer's note explaining why they changed it

  -- Computed diff statistics
  total_lines_generated INTEGER,
  total_lines_approved  INTEGER,
  lines_added           INTEGER DEFAULT 0,
  lines_removed         INTEGER DEFAULT 0,
  lines_unchanged       INTEGER DEFAULT 0,
  diff_summary          JSONB DEFAULT '[]',  -- array of {type, line, content}

  -- Training status
  embedded_in_vector    BOOLEAN DEFAULT false,
  used_in_training      BOOLEAN DEFAULT false,
  training_job_id       TEXT,

  -- Feedback metadata
  quality_rating        SMALLINT CHECK (quality_rating BETWEEN 1 AND 5),
  tags                  TEXT[] DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_gcode_diffs_part_number        ON gcode_diffs(part_number);
CREATE INDEX IF NOT EXISTS idx_gcode_diffs_geometry_hash      ON gcode_diffs(part_geometry_hash);
CREATE INDEX IF NOT EXISTS idx_gcode_diffs_embedded           ON gcode_diffs(embedded_in_vector);
CREATE INDEX IF NOT EXISTS idx_gcode_diffs_approved_at        ON gcode_diffs(approved_at DESC);

-- ─── DNC Machine Registry ─────────────────────────────────────────────────────

CREATE TYPE machine_protocol AS ENUM ('TCP_SOCKET', 'FTP', 'SMB', 'SFTP');
CREATE TYPE machine_status    AS ENUM ('ONLINE', 'OFFLINE', 'BUSY', 'ERROR', 'UNKNOWN');

CREATE TABLE IF NOT EXISTS machines (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  name          TEXT NOT NULL UNIQUE,   -- e.g. "HAAS-VF2-01"
  display_name  TEXT,
  controller    TEXT,                   -- "Fanuc 0i-MF", "Haas NGC"
  protocol      machine_protocol NOT NULL DEFAULT 'TCP_SOCKET',
  host          TEXT NOT NULL,
  port          INTEGER DEFAULT 8193,
  network_path  TEXT,                   -- for SMB/FTP: \\MACHINE\Programs
  username      TEXT,
  password_enc  TEXT,                   -- encrypted at rest
  is_active     BOOLEAN DEFAULT true,
  last_seen     TIMESTAMPTZ,
  status        machine_status DEFAULT 'UNKNOWN',
  notes         TEXT
);

-- ─── DNC Transfer Log ─────────────────────────────────────────────────────────

CREATE TYPE transfer_status AS ENUM ('PENDING', 'TRANSFERRING', 'SUCCESS', 'FAILED', 'CANCELLED');

CREATE TABLE IF NOT EXISTS dnc_transfers (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at    TIMESTAMPTZ,
  machine_id      UUID REFERENCES machines(id),
  machine_name    TEXT,
  gcode_diff_id   UUID REFERENCES gcode_diffs(id),
  program_name    TEXT,
  file_size_bytes INTEGER,
  transferred_by  UUID REFERENCES users(id),
  status          transfer_status DEFAULT 'PENDING',
  error_message   TEXT,
  checksum        TEXT          -- CRC32/MD5 of transferred file
);

CREATE INDEX IF NOT EXISTS idx_dnc_transfers_machine   ON dnc_transfers(machine_id);
CREATE INDEX IF NOT EXISTS idx_dnc_transfers_status    ON dnc_transfers(status);
CREATE INDEX IF NOT EXISTS idx_dnc_transfers_created   ON dnc_transfers(created_at DESC);

-- ─── LoRA Training Jobs ───────────────────────────────────────────────────────

CREATE TYPE training_status AS ENUM ('QUEUED', 'RUNNING', 'COMPLETE', 'FAILED', 'CANCELLED');

CREATE TABLE IF NOT EXISTS training_jobs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at      TIMESTAMPTZ,
  completed_at    TIMESTAMPTZ,
  triggered_by    UUID REFERENCES users(id),
  status          training_status DEFAULT 'QUEUED',
  base_model      TEXT NOT NULL,
  adapter_path    TEXT,
  dataset_size    INTEGER,          -- number of diff pairs used
  num_epochs      SMALLINT DEFAULT 3,
  loss_final      FLOAT,
  log_output      TEXT,
  error_message   TEXT
);

-- ─── Seed: Default Admin User ─────────────────────────────────────────────────
-- Password: "admin123" — CHANGE IN PRODUCTION
-- bcrypt hash for "admin123" with salt rounds=12
INSERT INTO users (email, name, password_hash, role, department)
VALUES (
  'admin@manufacturing.local',
  'System Administrator',
  '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMUurA1xOQXxkUxjv7JNjqCnhS',
  'ADMIN',
  'IT'
) ON CONFLICT (email) DO NOTHING;
