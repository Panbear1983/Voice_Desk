#!/bin/bash
# Shared secret + personal-data scanner for the Agent_Control_Desk repo (public remote).
# Dependency-free (pure grep). Called by pre-commit (staged) and pre-push (range).
# If `gitleaks` is installed it is ALSO run as a second, stronger layer.
#
# Usage:  _scan.sh <diff-source-command...>
#   the args are a command that prints the content to scan on stdout, e.g.
#     _scan.sh git diff --cached
#     _scan.sh git diff <remote_sha>..<local_sha>
# Exits non-zero (blocks the git action) if anything looks like a secret.
#
# Negative control: run scripts/test-hooks.sh after installing -- a scanner that passes
# everything looks identical to a working one.

set -uo pipefail
RED=$'\033[1;31m'; YEL=$'\033[1;33m'; NC=$'\033[0m'

# ---- high-signal secret value patterns (low false-positive) --------------
PATTERNS=(
  'sk-or-v1-[A-Za-z0-9]{20,}'                       # OpenRouter API key
  '[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}'                # Telegram bot token
  'sk-[A-Za-z0-9]{32,}'                             # OpenAI-style key
  'sk-ant-[A-Za-z0-9_-]{20,}'                       # Anthropic key
  'AIza[0-9A-Za-z_-]{35}'                           # Google API key
  'AKIA[0-9A-Z]{16}'                                # AWS access key id
  'gh[pousr]_[A-Za-z0-9]{36}'                       # GitHub tokens
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'             # private key blob
  '(OPENROUTER[A-Z_]*KEY|BRAVE_API_KEY|TELEGRAM_BOT_TOKEN|[A-Z_]*_SECRET|[A-Z_]*API_KEY|[A-Z_]*_TOKEN)[[:space:]]*[:=][[:space:]]*['"'"'"]?[A-Za-z0-9_.\-]{16,}'
  '/Users/[a-z]+/'                                  # absolute home paths (username leak)
  '[A-Za-z0-9._%+-]+@(gmail|hotmail|outlook|yahoo|icloud)\.com'  # personal email addresses
)
# placeholder values that are safe (templates/examples)
PLACEHOLDER='your_key_here|your_token_here|your_id_here|changeme|xxxx|<[^>]+>|example|placeholder|REDACTED|users\.noreply\.github\.com'

# ---- filenames that must never be committed ------------------------------
BLOCKED_FILES='(^|/)\.env$|(^|/)\.env\.[^e]|(^|/)[^/]*\.pem$|(^|/)[^/]*\.key$|(^|/)id_rsa|(^|/)credentials(/|$)|(^|/)auth-profiles\.json$|(^|/)data/progress|(^|/)data/cache/|\.pdf$|(^|/)exports/'

fail=0

# 1) filename guard (staged file list passed via env STAGED_FILES if set)
if [ -n "${STAGED_FILES:-}" ]; then
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    if printf '%s\n' "$f" | grep -qE "$BLOCKED_FILES"; then
      echo "${RED}BLOCKED file (never commit secrets/personal data/PDFs): $f${NC}"
      fail=1
    fi
  done <<< "$STAGED_FILES"
fi

# 2) content guard -- scan the diff produced by the passed command
content="$("$@" 2>/dev/null)"
for pat in "${PATTERNS[@]}"; do
  hits="$(printf '%s\n' "$content" | grep -aEn "^\+.*$pat" 2>/dev/null | grep -avE "$PLACEHOLDER")"
  if [ -n "$hits" ]; then
    echo "${RED}BLOCKED: possible secret / personal data matching /$pat/:${NC}"
    printf '%s\n' "$hits" | head -5
    fail=1
  fi
done

# 3) optional stronger layer if gitleaks is present
if command -v gitleaks >/dev/null 2>&1; then
  if ! gitleaks protect --staged --no-banner 2>/dev/null; then
    echo "${YEL}gitleaks flagged the staged changes (see above).${NC}"
    fail=1
  fi
fi

if [ "$fail" -ne 0 ]; then
  echo "${RED}--> Commit/push aborted by secret-scan guardrail.${NC}"
  echo "    If this is a false positive, review carefully, then bypass with: git ... --no-verify"
  exit 1
fi
exit 0
