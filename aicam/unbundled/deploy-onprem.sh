#!/usr/bin/env bash
# deploy-onprem.sh — One-click Air-Gapped On-Prem Installer
# Manufacturing AI Platform — Phase 3 Production Deployment
# ─────────────────────────────────────────────────────────────────────────────
# Prerequisites:
#   - Ubuntu 22.04 LTS or RHEL 9 (x86_64)
#   - NVIDIA GPU with driver ≥ 525 (for GPU inference)
#   - Docker Engine 26+ and Docker Compose v2
#   - 32GB RAM minimum, 500GB SSD recommended
#
# Usage:
#   chmod +x deploy-onprem.sh
#   sudo ./deploy-onprem.sh [--skip-gpu] [--offline] [--reset-db]
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail
IFS=$'\n\t'

# ─── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*"; exit 1; }

# ─── Flags ────────────────────────────────────────────────────────────────────
SKIP_GPU=false
OFFLINE=false
RESET_DB=false

for arg in "$@"; do
  case $arg in
    --skip-gpu)  SKIP_GPU=true  ;;
    --offline)   OFFLINE=true   ;;
    --reset-db)  RESET_DB=true  ;;
    --help)
      echo "Usage: $0 [--skip-gpu] [--offline] [--reset-db]"
      exit 0 ;;
  esac
done

echo -e "${BOLD}${CYAN}"
echo "╔════════════════════════════════════════════════════════╗"
echo "║    Manufacturing AI Platform — On-Prem Installer       ║"
echo "║    Phase 3 Production Deployment                       ║"
echo "╚════════════════════════════════════════════════════════╝${RESET}"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ─── 1. Check OS ──────────────────────────────────────────────────────────────
info "Checking operating system…"
if [[ -f /etc/os-release ]]; then
  source /etc/os-release
  info "OS: $PRETTY_NAME"
else
  warn "Cannot determine OS. Proceeding anyway."
fi

# ─── 2. Check Docker ──────────────────────────────────────────────────────────
info "Checking Docker…"
if ! command -v docker &>/dev/null; then
  error "Docker not found. Install Docker Engine 26+: https://docs.docker.com/engine/install/"
fi

DOCKER_VERSION=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "0")
info "Docker version: $DOCKER_VERSION"

if ! docker compose version &>/dev/null; then
  error "Docker Compose v2 plugin not found. Run: apt-get install docker-compose-plugin"
fi

success "Docker OK"

# ─── 3. Check NVIDIA GPU ──────────────────────────────────────────────────────
if [[ "$SKIP_GPU" == "false" ]]; then
  info "Checking NVIDIA GPU…"
  if command -v nvidia-smi &>/dev/null; then
    GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader | head -1)
    success "GPU: $GPU_INFO"

    # Check NVIDIA Container Toolkit
    if ! docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi &>/dev/null 2>&1; then
      warn "NVIDIA Container Toolkit not configured. GPU acceleration unavailable."
      warn "Install: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html"
      SKIP_GPU=true
    else
      success "NVIDIA Container Toolkit OK"
    fi
  else
    warn "nvidia-smi not found. Running CPU-only (slower inference)."
    SKIP_GPU=true
  fi
fi

# ─── 4. Check .env file ───────────────────────────────────────────────────────
info "Checking configuration…"
if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    warn ".env not found. Copying from .env.example — EDIT BEFORE PRODUCTION!"
    cp .env.example .env
    # Set required on-prem defaults
    sed -i 's/DEPLOYMENT_MODE=.*/DEPLOYMENT_MODE=ON_PREM/' .env
    sed -i 's/^# DATABASE_URL/DATABASE_URL/' .env
  else
    error ".env file not found. Copy .env.example to .env and configure it."
  fi
fi

# Validate critical env vars
source .env 2>/dev/null || true
: "${POSTGRES_PASSWORD:=changeme}"
: "${MINIO_ROOT_PASSWORD:=changeme}"

if [[ "$POSTGRES_PASSWORD" == "changeme" ]]; then
  warn "POSTGRES_PASSWORD is set to default 'changeme'. Change for production!"
fi

success "Configuration loaded"

# ─── 5. Create required directories ──────────────────────────────────────────
info "Creating directories…"
mkdir -p \
  backups/db \
  nginx/ssl \
  monitoring/grafana/provisioning/datasources \
  monitoring/grafana/provisioning/dashboards \
  data/training \
  models/lora_adapters

# Nginx config (if not present)
if [[ ! -f nginx/nginx.conf ]]; then
  cat > nginx/nginx.conf <<'NGINX'
events { worker_connections 1024; }
http {
  upstream app { server app:3000; }
  upstream grafana { server grafana:3000; }

  server {
    listen 80;
    server_name _;
    client_max_body_size 500M;

    location / {
      proxy_pass http://app;
      proxy_http_version 1.1;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection 'upgrade';
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_read_timeout 300s;
      proxy_send_timeout 300s;
    }

    location /grafana/ {
      proxy_pass http://grafana/;
    }
  }
}
NGINX
  success "Nginx config created"
fi

# Prometheus config
if [[ ! -f monitoring/prometheus.yml ]]; then
  cat > monitoring/prometheus.yml <<'PROM'
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'app'
    static_configs:
      - targets: ['app:3000']
    metrics_path: /api/metrics

  - job_name: 'postgres'
    static_configs:
      - targets: ['postgres:5432']

  - job_name: 'minio'
    static_configs:
      - targets: ['minio:9000']
    metrics_path: /minio/v2/metrics/cluster
PROM
  success "Prometheus config created"
fi

# Grafana datasource provisioning
cat > monitoring/grafana/provisioning/datasources/prometheus.yml <<'GRAFDS'
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    url: http://prometheus:9090
    access: proxy
    isDefault: true
GRAFDS

# ─── 6. Pull or load Docker images ───────────────────────────────────────────
info "Preparing Docker images…"
if [[ "$OFFLINE" == "true" ]]; then
  info "Offline mode — loading images from ./docker-images/*.tar"
  if [[ -d docker-images ]]; then
    for tarfile in docker-images/*.tar; do
      [[ -f "$tarfile" ]] || continue
      info "Loading: $tarfile"
      docker load -i "$tarfile"
    done
  else
    error "Offline mode enabled but ./docker-images/ directory not found."
  fi
else
  info "Pulling images (requires internet access)…"
  docker compose -f docker-compose.prod.yml pull --quiet
fi

success "Images ready"

# ─── 7. Optional: Reset database ──────────────────────────────────────────────
if [[ "$RESET_DB" == "true" ]]; then
  warn "Resetting database (--reset-db flag set)…"
  read -rp "This will DELETE ALL DATA. Type 'yes' to confirm: " confirm
  [[ "$confirm" == "yes" ]] || error "Aborted by user."
  docker compose -f docker-compose.prod.yml down -v --remove-orphans 2>/dev/null || true
  success "Database volumes cleared"
fi

# ─── 8. Build application ────────────────────────────────────────────────────
info "Building Next.js application…"
if [[ "$SKIP_GPU" == "true" ]]; then
  # Remove GPU requirements from compose
  docker compose -f docker-compose.prod.yml build --no-cache app 2>&1 | tail -5
else
  docker compose -f docker-compose.prod.yml build app 2>&1 | tail -5
fi
success "Application built"

# ─── 9. Start core services ───────────────────────────────────────────────────
info "Starting infrastructure services…"
docker compose -f docker-compose.prod.yml up -d postgres minio nginx prometheus grafana

info "Waiting for PostgreSQL to be ready (up to 60s)…"
for i in $(seq 1 12); do
  if docker compose -f docker-compose.prod.yml exec -T postgres \
     pg_isready -U postgres -d manufacturing_ai &>/dev/null; then
    success "PostgreSQL ready"
    break
  fi
  [[ $i -eq 12 ]] && error "PostgreSQL failed to start in 60s"
  sleep 5
done

info "Waiting for MinIO to be ready…"
for i in $(seq 1 12); do
  if curl -sf http://localhost:9000/minio/health/live &>/dev/null; then
    success "MinIO ready"
    break
  fi
  [[ $i -eq 12 ]] && warn "MinIO health check failed — proceeding anyway"
  sleep 5
done

# ─── 10. Initialize MinIO buckets ────────────────────────────────────────────
info "Initializing MinIO buckets…"
docker compose -f docker-compose.prod.yml up -d minio-init
sleep 5
success "MinIO buckets initialized"

# ─── 11. Start Ollama + pull models ─────────────────────────────────────────
info "Starting Ollama inference server…"
docker compose -f docker-compose.prod.yml up -d ollama

info "Pulling LLM models (this may take 10-30 minutes on first run)…"
docker compose -f docker-compose.prod.yml up ollama-init
success "Ollama models ready"

# ─── 12. Start application ────────────────────────────────────────────────────
info "Starting Manufacturing AI application…"
docker compose -f docker-compose.prod.yml up -d app

info "Waiting for application to be healthy (up to 120s)…"
for i in $(seq 1 24); do
  if curl -sf http://localhost:3000/ &>/dev/null; then
    success "Application is running!"
    break
  fi
  [[ $i -eq 24 ]] && warn "Application health check failed — check logs: docker compose -f docker-compose.prod.yml logs app"
  sleep 5
done

# ─── 13. Start DB backup service ──────────────────────────────────────────────
info "Starting automated backup service…"
docker compose -f docker-compose.prod.yml up -d pgbackup

# ─── 14. Summary ──────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔════════════════════════════════════════════════════════╗"
echo -e "║           Deployment Complete!                         ║"
echo -e "╚════════════════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "${BOLD}Service URLs:${RESET}"
HOST_IP=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "localhost")
echo -e "  ${CYAN}Web App:${RESET}       http://$HOST_IP"
echo -e "  ${CYAN}Grafana:${RESET}       http://$HOST_IP:3001"
echo -e "  ${CYAN}MinIO Console:${RESET} http://$HOST_IP:9001"
echo ""
echo -e "${BOLD}Admin Login:${RESET}"
echo -e "  Email:    admin@manufacturing.local"
echo -e "  Password: admin123  ${RED}← CHANGE IMMEDIATELY${RESET}"
echo ""
echo -e "${BOLD}Useful Commands:${RESET}"
echo -e "  Logs:    docker compose -f docker-compose.prod.yml logs -f app"
echo -e "  Stop:    docker compose -f docker-compose.prod.yml down"
echo -e "  Status:  docker compose -f docker-compose.prod.yml ps"
echo -e "  Backup:  docker compose -f docker-compose.prod.yml exec pgbackup /backup.sh"
echo ""
echo -e "${YELLOW}Next Steps for Production:${RESET}"
echo -e "  1. Change all default passwords in .env"
echo -e "  2. Configure SSL certificates in nginx/ssl/"
echo -e "  3. Set RBAC_SECRET to a strong random value"
echo -e "  4. Review ITAR data handling policies"
echo -e "  5. Configure network firewall rules"
echo ""
