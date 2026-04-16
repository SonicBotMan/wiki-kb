#!/usr/bin/env bash
# wiki-kb-sync.sh — wiki-kb 双向同步工具
#
# 源码在 NAS: /vol1/1000/opencode/wiki-kb/
# 部署在 NAS: /vol1/1000/wiki/scripts/ (bind mount → 容器)
# 远程备份:   GitHub SonicBotMan/wiki-kb
#
# 容器→GitHub 方向:
#   ./wiki-kb-sync.sh                          # 完整同步：导出→扫描→commit→push→验证
#   ./wiki-kb-sync.sh --check                  # 仅检查漂移，不推送
#   ./wiki-kb-sync.sh --files dream_cycle.py   # 仅同步指定文件
#
# NAS repo→容器 方向:
#   ./wiki-kb-sync.sh --deploy                 # 部署到容器（NAS本地cp→清缓存→重启→验证）
#   ./wiki-kb-sync.sh --deploy --push          # 部署 + 同步到 GitHub（完整流程）
#   ./wiki-kb-sync.sh --deploy --files xx.py   # 部署指定文件
#
# 其他:
#   ./wiki-kb-sync.sh --changelog "fix: xxx"   # 追加自定义 commit message
#
# Exit codes:
#   0  — 成功（或 --check 无漂移）
#   1  — 检测到漂移（--check 模式）
#   2  — 发现敏感信息（中止）
#   3  — 同步/验证失败
#   4  — 缺少前置依赖
#   5  — 部署后容器不健康

set -euo pipefail

# ============================================================
# Configuration
# ============================================================

NAS_USER="${NAS_USER:?NAS_USER not set}"
NAS_HOST="${NAS_HOST:?NAS_HOST not set}"
CONTAINER="${CONTAINER:-wiki-brain}"
REPO_URL="${REPO_URL:-https://github.com/SonicBotMan/wiki-kb.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
GIT_NAME="${GIT_NAME:-SonicBotMan}"
GIT_EMAIL="${GIT_EMAIL:?GIT_EMAIL not set}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-120}"

# Paths on NAS
NAS_REPO_DIR="/vol1/1000/opencode/wiki-kb"
NAS_DEPLOY_DIR="/vol1/1000/wiki/scripts"

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

# Root-level docs (synced in container→GitHub mode)
DOC_FILES=(
  README.md
  README_zh.md
  CHANGELOG.md
  SCHEMA.md
  RESOLVER.md
)

# .syncignore — 额外的安全漂移过滤模式
SYNCIGNORE_FILE="${SYNCIGNORE_FILE:-.syncignore}"

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

SSH_CMD="ssh ${NAS_USER}@${NAS_HOST}"

# ============================================================
# Parse Arguments
# ============================================================

MODE="sync"
SPECIFIC_FILES=()
COMMIT_MSG=""
DEPLOY_PUSH=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)       MODE="check"; shift ;;
    --deploy)      MODE="deploy"; shift ;;
    --push)        DEPLOY_PUSH=1; shift ;;
    --files)       shift ;;
    --changelog)   COMMIT_MSG="$2"; shift 2 ;;
    -h|--help)
      head -35 "$0" | grep '^#' | sed 's/^# \?//'
      exit 0
      ;;
    *)
      SPECIFIC_FILES+=("$1")
      shift
      ;;
  esac
done

if [[ $DEPLOY_PUSH -eq 1 && "$MODE" != "deploy" ]]; then
  die "--push only valid with --deploy (exit 4)" 4
fi

if [[ ${#SPECIFIC_FILES[@]} -gt 0 ]]; then
  SYNC_FILES=("${SPECIFIC_FILES[@]}")
else
  SYNC_FILES=("${ALL_SCRIPTS[@]}")
fi

WORK_DIR=$(mktemp -d /tmp/wiki-kb-sync.XXXXXX)

# ============================================================
# Prerequisites (shared)
# ============================================================

check_prerequisites() {
  log_step "Prerequisites"

  if ! $SSH_CMD -o ConnectTimeout=5 \
    "docker ps --filter name=${CONTAINER} --format '{{.Names}}'" 2>/dev/null | grep -q "$CONTAINER"; then
    die "Cannot reach container '${CONTAINER}' via SSH (exit 4)" 4
  fi
  log_ok "Container '${CONTAINER}' reachable"

  if ! command -v git &>/dev/null; then
    die "git not found (exit 4)" 4
  fi
  log_ok "git available"

  # Verify NAS repo exists
  if ! $SSH_CMD "test -d ${NAS_REPO_DIR}/.git" 2>/dev/null; then
    die "NAS repo not found at ${NAS_REPO_DIR} (exit 4)" 4
  fi
  log_ok "NAS repo: ${NAS_REPO_DIR}"

  # Verify deploy dir exists
  if ! $SSH_CMD "test -d ${NAS_DEPLOY_DIR}" 2>/dev/null; then
    die "Deploy dir not found at ${NAS_DEPLOY_DIR} (exit 4)" 4
  fi
  log_ok "Deploy dir: ${NAS_DEPLOY_DIR}"

  if command -v curl &>/dev/null; then
    if curl -sf -o /dev/null -w "" "http://${NAS_HOST}:8764/mcp" --connect-timeout 3 2>/dev/null; then
      log_ok "MCP endpoint responding"
    else
      log_warn "MCP endpoint not responding (container may be restarting)"
    fi
  fi
}

# ============================================================
# Build safe-diff filter from .syncignore
# ============================================================

build_safe_diff_filter() {
  SAFE_PATTERNS="NOTION_DB_|ntn_"
  if [[ -f "$SYNCIGNORE_FILE" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
      SAFE_PATTERNS="${SAFE_PATTERNS}|${line}"
    done < "$SYNCIGNORE_FILE"
    log_info "Loaded .syncignore patterns"
  fi
  echo "$SAFE_PATTERNS"
}

# ============================================================
# Deploy: NAS repo → NAS deploy dir → container
# ============================================================

do_deploy() {
  local files=("${SYNC_FILES[@]}")

  log_step "Deploy: NAS repo → container"

  # --- Step 1: py_compile in container (NAS host python3 has permission issues) ---
  log_info "Running py_compile in container..."
  COMPILE_FAIL=0
  for f in "${files[@]}"; do
    if [[ "$f" == *.py ]]; then
      if $SSH_CMD "docker exec ${CONTAINER} python3 -c \"
import py_compile, sys
try:
    py_compile.compile('/app/scripts/${f}', doraise=True)
    print('OK')
except py_compile.PyCompileError as e:
    print(f'FAIL: {e}')
    sys.exit(1)
\"" 2>/dev/null | grep -q "OK"; then
        log_ok "  ${f}: compile OK"
      else
        log_err "  ${f}: COMPILE FAILED"
        COMPILE_FAIL=1
      fi
    fi
  done
  if [[ $COMPILE_FAIL -eq 1 ]]; then
    die "py_compile failed — fix before deploy (exit 3)" 3
  fi

  # --- Step 2: Copy from NAS repo to NAS deploy dir (local cp, no scp needed) ---
  log_info "Copying files on NAS (${NAS_REPO_DIR}/scripts/ → ${NAS_DEPLOY_DIR}/)..."
  CP_FAIL=0
  for f in "${files[@]}"; do
    if $SSH_CMD "cp ${NAS_REPO_DIR}/scripts/${f} ${NAS_DEPLOY_DIR}/${f}" 2>/dev/null; then
      # Verify: compare first line
      local src_header dest_header
      src_header=$($SSH_CMD "head -1 ${NAS_REPO_DIR}/scripts/${f}" 2>/dev/null)
      dest_header=$($SSH_CMD "head -1 ${NAS_DEPLOY_DIR}/${f}" 2>/dev/null)
      if [[ "$src_header" == "$dest_header" ]]; then
        log_ok "  ${f}: copied & verified"
      else
        log_err "  ${f}: VERIFICATION FAILED"
        CP_FAIL=1
      fi
    else
      log_err "  ${f}: copy failed"
      CP_FAIL=1
    fi
  done
  if [[ $CP_FAIL -eq 1 ]]; then
    die "Some files failed to copy — aborting deploy (exit 3)" 3
  fi

  # --- Step 3: Clear __pycache__ ---
  log_info "Clearing __pycache__..."
  $SSH_CMD "rm -rf ${NAS_DEPLOY_DIR}/__pycache__" 2>/dev/null || true
  log_ok "__pycache__ cleared"

  # --- Step 4: Restart container ---
  log_info "Restarting container '${CONTAINER}'..."
  $SSH_CMD "docker restart ${CONTAINER}" 2>/dev/null || true

  # --- Step 5: Wait for healthy ---
  log_info "Waiting for container to become healthy (timeout=${HEALTH_TIMEOUT}s)..."
  HEALTH_OK=0
  ELAPSED=0
  while [[ $ELAPSED -lt $HEALTH_TIMEOUT ]]; do
    sleep 3
    ELAPSED=$((ELAPSED + 3))
    STATUS=$($SSH_CMD "docker inspect --format='{{.State.Health.Status}}' ${CONTAINER}" 2>/dev/null || echo "unknown")
    if [[ "$STATUS" == "healthy" ]]; then
      HEALTH_OK=1
      log_ok "Container healthy after ${ELAPSED}s"
      break
    fi
    if [[ $((ELAPSED % 15)) -eq 0 ]]; then
      log_info "  ... still waiting (${ELAPSED}s, status=${STATUS})"
    fi
  done
  if [[ $HEALTH_OK -eq 0 ]]; then
    log_err "Container did not become healthy within ${HEALTH_TIMEOUT}s"
    log_err "Last status: ${STATUS}"
    $SSH_CMD "docker logs ${CONTAINER} --tail 20" 2>/dev/null || true
    die "Container unhealthy after deploy (exit 5)" 5
  fi

  # --- Step 6: Post-deploy verification ---
  log_info "Running post-deploy verification..."

  ERRORS=$($SSH_CMD "docker logs ${CONTAINER} --tail 30 2>&1" | grep -i "error\|traceback\|exception" || true)
  if [[ -n "$ERRORS" ]]; then
    log_warn "Errors found in container logs:"
    echo "$ERRORS" | head -5
  else
    log_ok "No errors in container logs"
  fi

  if command -v curl &>/dev/null; then
    HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
      "http://${NAS_HOST}:8764/mcp" --connect-timeout 5 2>/dev/null || echo "000")
    if [[ "$HTTP_CODE" =~ ^[2-4] ]]; then
      log_ok "MCP endpoint: HTTP ${HTTP_CODE}"
    else
      log_warn "MCP endpoint: HTTP ${HTTP_CODE}"
    fi
  fi

  log_ok "Deploy complete"
}

# ============================================================
# Export & detect drift: container → local
# ============================================================

do_export_and_drift() {
  local files=("${SYNC_FILES[@]}")
  local clone_dir="$1"
  local safe_filter="$2"

  log_step "Exporting from container & detecting drift"

  DRIFT_COUNT=0
  DRIFT_FILES=()
  EXPORT_DIR="${WORK_DIR}/container"

  for f in "${files[@]}"; do
    mkdir -p "$(dirname "${EXPORT_DIR}/$f")"
    if $SSH_CMD \
      "docker exec ${CONTAINER} cat /app/scripts/$f" > "${EXPORT_DIR}/$f" 2>/dev/null; then

      if ! diff -q "${EXPORT_DIR}/$f" "${clone_dir}/scripts/$f" >/dev/null 2>&1; then
        SAFE_DIFF=0
        diff -q <(grep -vE "$safe_filter" "${EXPORT_DIR}/$f" 2>/dev/null || true) \
                 <(grep -vE "$safe_filter" "${clone_dir}/scripts/$f" 2>/dev/null || true) >/dev/null 2>&1 && SAFE_DIFF=1

        if [[ $SAFE_DIFF -eq 1 ]]; then
          log_ok "IGNORED: scripts/$f (safe diff: env-specific defaults)"
        else
          DRIFT_COUNT=$((DRIFT_COUNT + 1))
          DRIFT_FILES+=("scripts/$f")
          cp "${EXPORT_DIR}/$f" "${clone_dir}/scripts/$f"
          log_warn "DRIFT: scripts/$f"
        fi
      fi
    else
      log_warn "SKIP: scripts/$f (not found in container)"
    fi
  done

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
}

# ============================================================
# Sensitive info scan (CRITICAL aborts, WARNING prints only)
# ============================================================

do_sensitive_scan() {
  local scan_dir="$1"

  log_step "Sensitive information scan"

  SCAN_DIR="$scan_dir"
  CRITICAL_FOUND=0
  WARNING_FOUND=0

  scan_pattern() {
    local desc="$1" pattern="$2" severity="${3:-CRITICAL}"
    local matches
    matches=$(grep -rn -P "$pattern" \
      "$SCAN_DIR/scripts/" \
      "$SCAN_DIR/wiki-kb-sync.sh" \
      "$SCAN_DIR/README.md" \
      "$SCAN_DIR/README_zh.md" \
      "$SCAN_DIR/CHANGELOG.md" \
      "$SCAN_DIR/.env.example" \
      2>/dev/null || true)
    if [[ -n "$matches" ]]; then
      if [[ "$severity" == "CRITICAL" ]]; then
        log_err "${desc}: FOUND [CRITICAL]"
        CRITICAL_FOUND=1
      else
        log_warn "${desc}: FOUND [WARNING]"
        WARNING_FOUND=1
      fi
      echo "$matches" | head -5
    else
      log_ok "${desc}: clean"
    fi
  }

  # === CRITICAL ===
  scan_pattern "Phone numbers (CN)"       '1[3-9]\d{9}'
  scan_pattern "Hardcoded API key"        'api_key\s*=\s*['\''"][a-zA-Z0-9]'
  scan_pattern "Hardcoded password"       'password\s*=\s*['\''"]'
  scan_pattern "Hardcoded token"          'token\s*=\s*['\''"][a-zA-Z0-9]'
  scan_pattern "Hardcoded secret"         'secret\s*=\s*['\''"][a-zA-Z0-9]'
  scan_pattern "Bearer token"             'Bearer [a-zA-Z0-9_\-]{20,}'
  scan_pattern "GitHub token"             'gh[pousr]_[a-zA-Z0-9]{30,}'
  scan_pattern "Discord bot token"        '[MN][A-Za-z0-9]{23,}\.[A-Za-z0-9]{6}\.[A-Za-z0-9]{27,}'
  scan_pattern "Telegram bot token"       '\d{8,10}:[A-Za-z0-9_\-]{35}'
  scan_pattern "JWT"                      'eyJ[a-zA-Z0-9_-]{20,}\.eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}'

  # === WARNING (non-blocking) ===
  scan_pattern "Notion UUID"              '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' 'WARNING'
  scan_pattern "Private IP"               '192\.168\.|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.' 'WARNING'
  scan_pattern "Chinese text (3+ chars)"  '[\x{4e00}-\x{9fff}]{3,}' 'WARNING'
  scan_pattern "Email addresses"          '[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}' 'WARNING'

  if [[ $CRITICAL_FOUND -eq 1 ]]; then
    echo ""
    log_err "CRITICAL sensitive information detected — SYNC ABORTED"
    die "Exit 2" 2
  fi

  if [[ $WARNING_FOUND -eq 1 ]]; then
    echo ""
    log_warn "Warnings found (non-blocking). Review above if needed."
  fi
}

# ============================================================
# README bilingual check
# ============================================================

do_readme_check() {
  local clone_dir="$1"

  log_step "README bilingual alignment check"

  if [[ -f "${clone_dir}/README.md" && -f "${clone_dir}/README_zh.md" ]]; then
    en_lines=$(grep -c '^## ' "${clone_dir}/README.md" || true)
    zh_lines=$(grep -c '^## ' "${clone_dir}/README_zh.md" || true)
    if [[ "$en_lines" -eq "$zh_lines" ]]; then
      log_ok "README sections aligned (${en_lines} sections each)"
    else
      log_warn "README section count mismatch: EN=${en_lines} ZH=${zh_lines}"
    fi

    if grep -q '\[English\](README.md)' "${clone_dir}/README.md" && \
       grep -q '\[English\](README.md)' "${clone_dir}/README_zh.md"; then
      log_ok "Language switch links present"
    else
      log_warn "Missing language switch links in README"
    fi
  else
    log_warn "README or README_zh.md missing — bilingual check skipped"
  fi
}

# ============================================================
# CHANGELOG gate
# ============================================================

do_changelog_check() {
  local clone_dir="$1"

  log_step "CHANGELOG check"

  if [[ ! -f "${clone_dir}/CHANGELOG.md" ]]; then
    log_err "CHANGELOG.md missing"
    return 1
  fi

  local changelog_dirty staged last_commit_files
  changelog_dirty=$(git -C "$clone_dir" diff --name-only HEAD -- CHANGELOG.md 2>/dev/null || true)
  staged=$(git -C "$clone_dir" diff --cached --name-only -- CHANGELOG.md 2>/dev/null || true)

  if [[ -n "$changelog_dirty" || -n "$staged" ]]; then
    log_ok "CHANGELOG.md has been updated"
    return 0
  fi

  last_commit_files=$(git -C "$clone_dir" diff --name-only HEAD~1 HEAD 2>/dev/null || true)
  if echo "$last_commit_files" | grep -q "CHANGELOG.md"; then
    log_ok "CHANGELOG.md updated in latest commit"
    return 0
  fi

  log_warn "CHANGELOG.md not updated in this changeset"
  log_warn "Consider adding a CHANGELOG entry before pushing"
  return 0
}

# ============================================================
# Commit & push
# ============================================================

do_commit_push() {
  local clone_dir="$1"

  if [[ $DRIFT_COUNT -eq 0 && -z "$COMMIT_MSG" ]]; then
    log_ok "Nothing to commit"
    return 0
  fi

  log_step "Commit & push"

  if [[ $DRIFT_COUNT -gt 0 && -z "$COMMIT_MSG" ]]; then
    log_err "Commit message required when files have changed"
    log_err "Usage: $0 --changelog \"type: description\""
    die "Exit 4" 4
  fi

  git -C "$clone_dir" add -A

  echo ""
  git -C "$clone_dir" diff --cached --stat
  echo ""

  git -C "$clone_dir" commit -m "$COMMIT_MSG"

  log_info "Pushing to ${REPO_BRANCH}..."
  if ! git -C "$clone_dir" push origin "$REPO_BRANCH" 2>&1; then
    die "Push failed (exit 3)" 3
  fi
  log_ok "Pushed successfully"
}

# ============================================================
# Post-push verification
# ============================================================

do_post_push_verify() {
  local clone_dir="$1"
  local safe_filter="$2"

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
      if diff -q <(grep -vE "$safe_filter" "${EXPORT_DIR}/$f" 2>/dev/null || true) \
               <(grep -vE "$safe_filter" "${VERIFY_DIR}/scripts/$f" 2>/dev/null || true) >/dev/null 2>&1; then
        log_ok "VERIFIED (safe diff): scripts/$f"
      else
        log_err "VERIFY FAILED: scripts/$f"
        VERIFY_FAIL=1
      fi
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
}

# ============================================================
# Sync NAS repo back to NAS deploy dir (used by --deploy --push)
# ============================================================

sync_nas_repo_to_deploy() {
  log_info "Syncing NAS repo scripts to deploy dir..."
  for f in "${SYNC_FILES[@]}"; do
    $SSH_CMD "cp ${NAS_REPO_DIR}/scripts/${f} ${NAS_DEPLOY_DIR}/${f}" 2>/dev/null || true
  done
  $SSH_CMD "rm -rf ${NAS_DEPLOY_DIR}/__pycache__" 2>/dev/null || true
  log_ok "NAS repo synced to deploy dir"
}

# ============================================================
# Main: dispatch by mode
# ============================================================

check_prerequisites
SAFE_FILTER=$(build_safe_diff_filter)

case "$MODE" in
  deploy)
    do_deploy

    if [[ $DEPLOY_PUSH -eq 1 ]]; then
      log_step "Deploy complete, now syncing to GitHub..."

      CLONE_DIR="${WORK_DIR}/wiki-kb"
      if ! git clone --depth 1 "${REPO_URL}" "$CLONE_DIR" 2>&1 | tail -1; then
        die "Failed to clone ${REPO_URL} (exit 3)" 3
      fi
      cd "$CLONE_DIR"
      git config user.name "$GIT_NAME"
      git config user.email "$GIT_EMAIL"

      # Also sync any files from deploy dir back to clone (container may have env-specific values)
      do_export_and_drift "$CLONE_DIR" "$SAFE_FILTER"
      do_sensitive_scan "$CLONE_DIR"
      do_readme_check "$CLONE_DIR"
      do_changelog_check "$CLONE_DIR"
      do_commit_push "$CLONE_DIR"
      do_post_push_verify "$CLONE_DIR" "$SAFE_FILTER"

      # Sync NAS repo with what we just pushed (pull latest from GitHub)
      $SSH_CMD "cd ${NAS_REPO_DIR} && git fetch origin main && git reset --hard origin/main" 2>/dev/null
      log_ok "NAS repo updated from GitHub"
    fi

    echo ""
    log_step "Deploy Summary"
    log_ok "Files deployed: ${#SYNC_FILES[@]}"
    log_ok "Container: ${CONTAINER} (healthy)"
    log_ok "NAS repo: ${NAS_REPO_DIR}"
    if [[ $DEPLOY_PUSH -eq 1 ]]; then
      log_ok "GitHub: synced"
    fi
    echo ""
    ;;

  check|sync)
    log_step "Cloning GitHub repo"

    CLONE_DIR="${WORK_DIR}/wiki-kb"
    if ! git clone --depth 1 "${REPO_URL}" "$CLONE_DIR" 2>&1 | tail -1; then
      die "Failed to clone ${REPO_URL} (exit 3)" 3
    fi
    cd "$CLONE_DIR"
    git config user.name "$GIT_NAME"
    git config user.email "$GIT_EMAIL"
    log_ok "Cloned to ${CLONE_DIR}"

    do_export_and_drift "$CLONE_DIR" "$SAFE_FILTER"
    do_sensitive_scan "$CLONE_DIR"
    do_readme_check "$CLONE_DIR"
    do_changelog_check "$CLONE_DIR"
    do_commit_push "$CLONE_DIR"
    do_post_push_verify "$CLONE_DIR" "$SAFE_FILTER"

    # Sync NAS repo with GitHub after push
    if [[ $DRIFT_COUNT -gt 0 ]]; then
      $SSH_CMD "cd ${NAS_REPO_DIR} && git fetch origin main && git reset --hard origin/main" 2>/dev/null
      sync_nas_repo_to_deploy
      log_ok "NAS repo synced from GitHub"
    fi

    echo ""
    log_step "Summary"
    log_ok "Files synced: ${DRIFT_COUNT}"
    log_ok "Commit: $(git -C "$CLONE_DIR" rev-parse --short HEAD)"
    log_ok "Message: ${COMMIT_MSG:-no changes}"
    log_ok "NAS repo: ${NAS_REPO_DIR}"
    echo ""
    ;;

  *)
    die "Unknown mode: ${MODE} (exit 4)" 4
    ;;
esac
