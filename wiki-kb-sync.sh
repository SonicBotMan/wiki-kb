#!/usr/bin/env bash
# wiki-kb-sync.sh — One-command sync: Docker container → GitHub
#
# Usage:
#   ./wiki-kb-sync.sh                          # Full sync (export → scan → commit → push → verify)
#   ./wiki-kb-sync.sh --check                  # Check drift only, no push
#   ./wiki-kb-sync.sh --files dream_cycle.py   # Sync specific files only
#   ./wiki-kb-sync.sh --changelog "fix: xxx"   # Append custom message to CHANGELOG
#
# Exit codes:
#   0  — Success (or --check mode with no drift)
#   1  — Drift detected (--check mode)
#   2  — Sensitive info found (sync aborted)
#   3  — Sync/verify failed
#   4  — Missing prerequisites
    
set -euo pipefail
    
# ============================================================
# Configuration
# ============================================================
    
NAS_USER="${NAS_USER:-REDACTED_USER}"
NAS_HOST="${NAS_HOST:-REDACTED_IP}"
CONTAINER="${CONTAINER:-wiki-brain}"
REPO_URL="${REPO_URL:-https://github.com/SonicBotMan/wiki-kb.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
GIT_NAME="${GIT_NAME:-SonicBotMan}"
GIT_EMAIL="${GIT_EMAIL:-REDACTED_EMAIL}"
    
# All syncable script files
ALL_SCRIPTS=(
  auto_index.py
  dream_cycle.py
  entity_registry.py
  memory_to_wiki.py
  migrate_wiki_schema.py
  wiki_config.py
  wiki_mcp_server.py
  wiki-to-notion.py
  wiki_utils.py
  wiki-backup.sh
)
    
# Root-level syncable files
ROOT_FILES=(
  Dockerfile
  docker-compose.yml
  docker-compose.production.yml
  requirements.txt
  .env.example
  README.md
  README_zh.md
  CHANGELOG.md
  SCHEMA.md
  RESOLVER.md
)
    
# ============================================================
# Colors & Helpers
# ============================================================
    
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'
    
log_info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_err()   { echo -e "${RED}[FAIL]${NC}  $*"; }
log_step()  { echo -e "\n${CYAN}━━━ $* ━━━${NC}"; }
    
die() { log_err "$*"; exit "$1"; }
    
cleanup() {
  rm -rf "$WORK_DIR" 2>/dev/null
}
trap cleanup EXIT
    
# ============================================================
# Parse Arguments
# ============================================================
    
MODE="sync"
SPECIFIC_FILES=()
CUSTOM_MSG=""
    
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)       MODE="check"; shift ;;
    --files)       MODE="files"; shift ;;
    --changelog)   CUSTOM_MSG="$2"; shift 2 ;;
    -h|--help)
      head -20 "$0" | grep '^#' | sed 's/^# \?//'
      exit 0
      ;;
    *)
      if [[ "$MODE" == "files" ]]; then
        SPECIFIC_FILES+=("$1")
      else
        die "Unknown argument: $1 (use --help)" 4
      fi
      shift
      ;;
  esac
done
    
# ============================================================
# Prerequisites
# ============================================================
    
WORK_DIR=$(mktemp -d /tmp/wiki-kb-sync.XXXXXX)
    
log_step "Prerequisites"
    
# Check SSH connectivity
if ! ssh -o ConnectTimeout=5 "${NAS_USER}@${NAS_HOST}" "docker ps --filter name=${CONTAINER} --format '{{.Names}}'" 2>/dev/null | grep -q "$CONTAINER"; then
  die "Cannot reach container '${CONTAINER}' via SSH (exit 4)" 4
fi
log_ok "Container '${CONTAINER}' reachable"
    
# Check git available
if ! command -v git &>/dev/null; then
  die "git not found (exit 4)" 4
fi
log_ok "git available"
    
# Check MCP endpoint
if curl -sf -o /dev/null -w "" "http://${NAS_HOST}:8764/mcp" --connect-timeout 3 2>/dev/null; then
  log_ok "MCP endpoint responding"
else
  log_warn "MCP endpoint not responding (container may be restarting)"
fi
    
# ============================================================
# Step 1: Clone GitHub repo
# ============================================================
    
log_step "Cloning GitHub repo"
    
CLONE_DIR="${WORK_DIR}/wiki-kb"
if ! git clone --depth 1 "${REPO_URL}" "$CLONE_DIR" 2>&1 | tail -1; then
  die "Failed to clone ${REPO_URL} (exit 3)" 3
fi
    
cd "$CLONE_DIR"
git config user.name "$GIT_NAME"
git config user.email "$GIT_EMAIL"
log_ok "Cloned to ${CLONE_DIR}"
    
# ============================================================
# Step 2: Determine which files to sync
# ============================================================
    
if [[ "$MODE" == "files" ]]; then
  SYNC_FILES=("${SPECIFIC_FILES[@]}")
  log_info "Syncing specific files: ${SYNC_FILES[*]}"
elif [[ "$MODE" == "check" ]]; then
  SYNC_FILES=("${ALL_SCRIPTS[@]}")
else
  SYNC_FILES=("${ALL_SCRIPTS[@]}")
fi
    
# ============================================================
# Step 3: Export files from container & detect drift
# ============================================================
    
log_step "Exporting from container & detecting drift"
    
DRIFT_COUNT=0
DRIFT_FILES=()
EXPORT_DIR="${WORK_DIR}/container"
    
for f in "${SYNC_FILES[@]}"; do
  mkdir -p "$(dirname "${EXPORT_DIR}/$f")"
  if ssh "${NAS_USER}@${NAS_HOST}" "docker exec ${CONTAINER} cat /app/scripts/$f" > "${EXPORT_DIR}/$f" 2>/dev/null; then
    if ! diff -q "${EXPORT_DIR}/$f" "${CLONE_DIR}/scripts/$f" >/dev/null 2>&1; then
      # Check if diff is only in known-safe patterns (Notion UUID defaults, env-specific values)
      SAFE_DIFF=0
      if diff -q <(grep -v 'NOTION_DB_\|ntn_' "${EXPORT_DIR}/$f" 2>/dev/null || true) \
                 <(grep -v 'NOTION_DB_\|ntn_' "${CLONE_DIR}/scripts/$f" 2>/dev/null || true) >/dev/null 2>&1; then
        SAFE_DIFF=1
        log_ok "IGNORED: scripts/$f (safe diff: Notion UUID defaults)"
      fi
      if [[ $SAFE_DIFF -eq 0 ]]; then
        DRIFT_COUNT=$((DRIFT_COUNT + 1))
        DRIFT_FILES+=("scripts/$f")
        cp "${EXPORT_DIR}/$f" "${CLONE_DIR}/scripts/$f"
        log_warn "DRIFT: scripts/$f"
      fi
    fi
  else
    log_warn "SKIP: scripts/$f (not found in container)"
  fi
done
    
# Also check root files (only in full sync mode)
if [[ "$MODE" == "sync" ]]; then
  for f in "${ROOT_FILES[@]}"; do
    if [[ ! -f "${CLONE_DIR}/$f" ]]; then
      continue
    fi
  done
fi
    
echo ""
if [[ $DRIFT_COUNT -eq 0 ]]; then
  log_ok "No drift detected — container and GitHub are in sync"
  if [[ "$MODE" == "check" ]]; then
    exit 0
  fi
else
  log_warn "${DRIFT_COUNT} file(s) drifted: ${DRIFT_FILES[*]}"
  if [[ "$MODE" == "check" ]]; then
    echo ""
    log_info "Run without --check to sync these files"
    exit 1
  fi
fi
    
# ============================================================
# Step 4: Sensitive info scan (MANDATORY)
# ============================================================
    
log_step "Sensitive information scan"
    
SCAN_DIR="${CLONE_DIR}"
SENSITIVE_FOUND=0
    
scan_pattern() {
  local desc="$1" pattern="$2" severity="${3:-CRITICAL}"
  local matches
  matches=$(grep -rn -P "$pattern" "$SCAN_DIR/scripts/" "$SCAN_DIR/README.md" "$SCAN_DIR/README_zh.md" "$SCAN_DIR/CHANGELOG.md" "$SCAN_DIR/.env.example" 2>/dev/null || true)
  if [[ -n "$matches" ]]; then
    log_err "${desc}: FOUND [${severity}]"
    echo "$matches" | head -10
    SENSITIVE_FOUND=1
  else
    log_ok "${desc}: clean"
  fi
}
    
# === CRITICAL: must fix before push ===
    
scan_pattern "Phone numbers (CN)"       '1[3-9]\d{9}'
scan_pattern "Notion UUID"              '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
scan_pattern "Private IP"               '192\.168\.|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.'
scan_pattern "Hardcoded API key"        'api_key\s*=\s*['\''"][a-zA-Z0-9]'
scan_pattern "Hardcoded password"       'password\s*=\s*['\''"]'
scan_pattern "Hardcoded token"          'token\s*=\s*['\''"][a-zA-Z0-9]'
scan_pattern "Hardcoded secret"         'secret\s*=\s*['\''"][a-zA-Z0-9]'
scan_pattern "Bearer token"             'Bearer [a-zA-Z0-9_\-]{20,}'
scan_pattern "GitHub token"             'gh[pousr]_[a-zA-Z0-9]{30,}'
scan_pattern "Discord bot token"        '[MN][A-Za-z0-9]{23,}\.[A-Za-z0-9]{6}\.[A-Za-z0-9]{27,}'
scan_pattern "Telegram bot token"       '\d{8,10}:[A-Za-z0-9_\-]{35,}'
scan_pattern "Generic token/credential" 'eyJ[a-zA-Z0-9_-]{20,}\.eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}'
    
# === WARNING: review before push (may be false positives) ===
    
# Chinese names (3+ consecutive CJK chars — may false-positive on common words)
scan_pattern "Chinese names"            '[\x{4e00}-\x{9fff}]{3,}' 'WARNING'
    
# Company names (known patterns — extend this list as needed)
scan_pattern "Known company names"      '腾讯|阿里巴巴|华为|字节跳动|百度|京东|美团|拼多多|小米|网易|蚂蚁|滴滴|快手|B站|bilibili|Tencent|Alibaba' 'WARNING'
    
# Chinese addresses (province/city/district keywords)
scan_pattern "Chinese addresses"        '省|市|区|县|镇|村|路[0-9]|号[0-9]|弄|室|栋|楼层|座|院' 'WARNING'
    
# Email addresses (may contain personal info)
scan_pattern "Email addresses"          '[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}' 'WARNING'
    
if [[ $SENSITIVE_FOUND -eq 1 ]]; then
  echo ""
  log_err "SENSITIVE INFORMATION DETECTED — SYNC ABORTED"
  log_err "Fix the issues above before retrying"
  die "Exit 2" 2
fi
    
# ============================================================
# Step 5: README bilingual check
# ============================================================
    
log_step "README bilingual alignment check"
    
if [[ -f "${CLONE_DIR}/README.md" && -f "${CLONE_DIR}/README_zh.md" ]]; then
  en_lines=$(grep -c '^## ' "${CLONE_DIR}/README.md" || true)
  zh_lines=$(grep -c '^## ' "${CLONE_DIR}/README_zh.md" || true)
  if [[ "$en_lines" -eq "$zh_lines" ]]; then
    log_ok "README sections aligned (${en_lines} sections each)"
  else
    log_warn "README section count mismatch: EN=${en_lines} ZH=${zh_lines}"
    log_warn "Please verify bilingual alignment manually"
  fi
    
  # Check language switch links
  if grep -q '\[English\](README.md)' "${CLONE_DIR}/README.md" && \
     grep -q '\[English\](README.md)' "${CLONE_DIR}/README_zh.md"; then
    log_ok "Language switch links present"
  else
    log_warn "Missing language switch links in README"
  fi
else
  log_warn "README_zh.md missing — bilingual check skipped"
fi
    
# ============================================================
# Step 6: Generate commit message & push
# ============================================================
    
if [[ $DRIFT_COUNT -eq 0 && -z "$CUSTOM_MSG" ]]; then
  log_ok "Nothing to commit"
  exit 0
fi
    
log_step "Commit & push"
    
# Generate commit message from drifted files
if [[ -n "$CUSTOM_MSG" ]]; then
  COMMIT_MSG="$CUSTOM_MSG"
elif [[ $DRIFT_COUNT -gt 0 ]]; then
  # Categorize changes
  TYPES=""
  for f in "${DRIFT_FILES[@]}"; do
    fname=$(basename "$f")
    case "$fname" in
      *_test*|test_*)  TYPES="${TYPES}test: " ;;
      *_fix*|bug_*)    TYPES="${TYPES}fix: " ;;
      *new*|*_new*)    TYPES="${TYPES}feat: " ;;
      *)               TYPES="${TYPES}sync: " ;;
    esac
  done
  TYPES=$(echo "$TYPES" | tr ' ' '\n' | sort -u | head -1)
  FILE_LIST=$(printf '%s, ' "${DRIFT_FILES[@]}" | sed 's/, $//')
  COMMIT_MSG="${TYPES}sync container code (${FILE_LIST})"
else
  COMMIT_MSG="docs: update documentation"
fi
    
git add -A
    
# Show staged changes summary
STAGED=$(git diff --cached --stat)
echo "$STAGED"
echo ""
    
git commit -m "$COMMIT_MSG"
    
log_info "Pushing to ${REPO_BRANCH}..."
if ! git push origin "$REPO_BRANCH" 2>&1; then
  die "Push failed (exit 3)" 3
fi
log_ok "Pushed successfully"
    
# ============================================================
# Step 7: Post-push verification
# ============================================================
    
log_step "Post-push verification"
    
VERIFY_DIR="${WORK_DIR}/verify"
if ! git clone --depth 1 "${REPO_URL}" "$VERIFY_DIR" 2>&1 | tail -1; then
  die "Failed to clone for verification (exit 3)" 3
fi
    
VERIFY_FAIL=0
for f in "${SYNC_FILES[@]}"; do
  if [[ ! -f "${EXPORT_DIR}/$f" ]]; then
    continue
  fi
  if ! diff -q "${EXPORT_DIR}/$f" "${VERIFY_DIR}/scripts/$f" >/dev/null 2>&1; then
    log_err "VERIFY FAILED: scripts/$f"
    VERIFY_FAIL=1
  fi
done
    
if [[ $VERIFY_FAIL -eq 0 ]]; then
  echo ""
  log_ok "All files verified — container and GitHub are in sync ✅"
else
  echo ""
  log_err "Verification failed — some files don't match after push"
  die "Exit 3" 3
fi
    
# ============================================================
# Summary
# ============================================================
    
echo ""
log_step "Summary"
log_ok "Files synced: ${DRIFT_COUNT}"
log_ok "Commit: $(git -C "$CLONE_DIR" rev-parse --short HEAD)"
log_ok "Message: ${COMMIT_MSG}"
echo ""
