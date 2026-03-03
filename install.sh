#!/usr/bin/env bash
set -euo pipefail

CODEOPS_REPO_DEFAULT="ZAaiyan/codeops"
CODEOPS_REPO="${CODEOPS_REPO:-$CODEOPS_REPO_DEFAULT}"
CODEOPS_REPO_URL_DEFAULT="https://github.com/$CODEOPS_REPO"
CODEOPS_REPO_URL="${CODEOPS_REPO_URL:-$CODEOPS_REPO_URL_DEFAULT}"
CODEOPS_REF="${CODEOPS_REF:-main}"
CODEOPS_HOME="${CODEOPS_HOME:-$HOME/.local/codeops}"
CODEOPS_BIN_DIR="${CODEOPS_BIN_DIR:-$HOME/.local/bin}"
CODEOPS_BASE_URL="${CODEOPS_BASE_URL:-https://ark.cn-beijing.volces.com/api/v3}"
CODEOPS_MODEL="${CODEOPS_MODEL:-}"
CODEOPS_CONFIGURE="${CODEOPS_CONFIGURE:-0}"

log() { printf '%s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing dependency: $1"
}base_url: https://ark.cn-beijing.volces.com/api/v3
model: ep-你的真实endpoint
max_iterations: 12
temperature: 0

detect_shell_rc() {
  local shell_name=""
  shell_name="$(basename "${SHELL:-}")"

  if [ "$shell_name" = "zsh" ]; then
    if [ -f "$HOME/.zprofile" ]; then
      printf '%s' "$HOME/.zprofile"
      return
    fi
    printf '%s' "$HOME/.zprofile"
    return
  fi

  if [ "$shell_name" = "bash" ]; then
    if [ -f "$HOME/.bashrc" ]; then
      printf '%s' "$HOME/.bashrc"
      return
    fi
    if [ -f "$HOME/.bash_profile" ]; then
      printf '%s' "$HOME/.bash_profile"
      return
    fi
    printf '%s' "$HOME/.bashrc"
    return
  fi

  printf '%s' "$HOME/.profile"
}

python_version_ok() {
  local py="$1"
  "$py" - <<'PY'
import sys
major, minor = sys.version_info[:2]
if (major, minor) < (3, 11):
    raise SystemExit(1)
PY
}

is_macos() {
  [ "$(uname -s 2>/dev/null || true)" = "Darwin" ]
}

ensure_python311_macos_brew() {
  if ! is_macos; then
    return 1
  fi
  if ! command -v brew >/dev/null 2>&1; then
    return 1
  fi

  if brew list --versions python@3.11 >/dev/null 2>&1; then
    return 0
  fi

  log "Python >= 3.11 not found. Installing python@3.11 via Homebrew..."
  brew install python@3.11
  return 0
}

find_python() {
  local preferred=(
    "${CODEOPS_PYTHON:-}"
    "python3.13"
    "python3.12"
    "python3.11"
    "python3"
  )

  for candidate in "${preferred[@]}"; do
    if [ -z "$candidate" ]; then
      continue
    fi
    if ! command -v "$candidate" >/dev/null 2>&1; then
      continue
    fi
    if python_version_ok "$candidate"; then
      command -v "$candidate"
      return
    fi
  done

  if ensure_python311_macos_brew; then
    local brew_py=""
    brew_py="$(brew --prefix python@3.11 2>/dev/null || true)/bin/python3.11"
    if [ -x "$brew_py" ] && python_version_ok "$brew_py"; then
      printf '%s' "$brew_py"
      return
    fi
    if command -v python3.11 >/dev/null 2>&1 && python_version_ok python3.11; then
      command -v python3.11
      return
    fi
  fi

  die "Python >= 3.11 not found. Install Python 3.11+ and ensure it's on PATH (e.g. python3.11), or set CODEOPS_PYTHON=/path/to/python3.11"
}

append_path_once() {
  local rc_file="$1"
  local bin_dir="$2"
  local marker_begin="# >>> codeops installer >>>"
  local marker_end="# <<< codeops installer <<<"

  mkdir -p "$(dirname "$rc_file")" 2>/dev/null || true
  touch "$rc_file" 2>/dev/null || true

  if grep -Fq "$marker_begin" "$rc_file" 2>/dev/null; then
    return
  fi

  {
    printf '\n%s\n' "$marker_begin"
    printf 'export PATH="%s:$PATH"\n' "$bin_dir"
    printf '%s\n' "$marker_end"
  } >>"$rc_file"
}

is_interactive() {
  [ -t 0 ] && [ -t 1 ]
}

configure_auth() {
  local codeops_bin="$1"
  local base_url="$2"
  local model="$3"

  if [ -z "$model" ] && is_interactive; then
    printf 'Ark Endpoint ID (e.g. ep-xxxx): ' > /dev/tty
    IFS= read -r model < /dev/tty || true
    model="$(printf '%s' "$model" | tr -d '\r' | xargs)"
  fi

  if [ -z "$model" ]; then
    warn "skip auth config: missing CODEOPS_MODEL (Endpoint ID)"
    return 0
  fi

  "$codeops_bin" auth login --base-url "$base_url" --model "$model"
  return 0
}

repo_slug_from_url() {
  local url="$1"
  url="${url%/}"
  url="${url%.git}"
  url="${url#https://}"
  url="${url#http://}"
  url="${url#github.com/}"
  url="${url#www.github.com/}"
  printf '%s' "$url"
}

download_and_extract_repo() {
  local repo_url="$1"
  local ref="$2"

  if [[ "$repo_url" == file://* ]]; then
    local local_path="${repo_url#file://}"
    if [ -d "$local_path" ]; then
      printf '%s' "$local_path"
      return
    fi
    die "local repo path not found: $local_path"
  fi
  if [[ "$repo_url" == /* ]]; then
    if [ -d "$repo_url" ]; then
      printf '%s' "$repo_url"
      return
    fi
    die "local repo path not found: $repo_url"
  fi

  local slug=""
  slug="$(repo_slug_from_url "$repo_url")"
  if [[ "$slug" != */* ]]; then
    die "invalid repo url: $repo_url (expected https://github.com/<owner>/<repo>)"
  fi

  local tmp_dir=""
  tmp_dir="$(mktemp -d)"

  local urls=(
    "https://github.com/$slug/archive/refs/heads/$ref.tar.gz"
    "https://github.com/$slug/archive/refs/tags/$ref.tar.gz"
    "https://github.com/$slug/archive/$ref.tar.gz"
  )

  local archive="$tmp_dir/repo.tar.gz"
  local ok="0"
  for u in "${urls[@]}"; do
    if curl -fsSL "$u" -o "$archive"; then
      ok="1"
      break
    fi
  done
  if [ "$ok" != "1" ]; then
    die "failed to download repo archive. repo=$repo_url ref=$ref"
  fi

  tar -xzf "$archive" -C "$tmp_dir"

  local extracted=""
  extracted="$(find "$tmp_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1 || true)"
  if [ -z "$extracted" ]; then
    die "failed to extract repo archive"
  fi

  printf '%s' "$extracted"
}

main() {
  need_cmd bash
  need_cmd curl

  log "Installing codeops..."
  log "Repo: $CODEOPS_REPO_URL (ref: $CODEOPS_REF)"

  local python_bin=""
  python_bin="$(find_python)"
  log "Python: $("$python_bin" -V 2>&1 | awk '{print $2}') ($python_bin)"

  mkdir -p "$CODEOPS_HOME"
  mkdir -p "$CODEOPS_BIN_DIR"

  local venv_dir="$CODEOPS_HOME/venv"
  if [ ! -x "$venv_dir/bin/python" ]; then
    log "Creating venv: $venv_dir"
    "$python_bin" -m venv "$venv_dir"
  fi

  log "Upgrading pip..."
  "$venv_dir/bin/python" -m pip install -U pip >/dev/null

  log "Downloading source..."
  local src_dir=""
  src_dir="$(download_and_extract_repo "$CODEOPS_REPO_URL" "$CODEOPS_REF")"

  log "Installing package..."
  "$venv_dir/bin/python" -m pip install -U "$src_dir"

  if [ ! -x "$venv_dir/bin/codeops" ]; then
    die "installation succeeded but 'codeops' entrypoint not found at $venv_dir/bin/codeops"
  fi

  log "Linking executable into: $CODEOPS_BIN_DIR/codeops"
  ln -sf "$venv_dir/bin/codeops" "$CODEOPS_BIN_DIR/codeops"
  chmod +x "$CODEOPS_BIN_DIR/codeops" || true

  local codeops_bin="$CODEOPS_BIN_DIR/codeops"
  if [ "$CODEOPS_CONFIGURE" = "1" ]; then
    if is_interactive; then
      log ""
      log "Configuring global auth..."
      configure_auth "$codeops_bin" "$CODEOPS_BASE_URL" "$CODEOPS_MODEL"
    else
      warn "skip auth config: non-interactive shell. set CODEOPS_CONFIGURE=1 CODEOPS_MODEL=ep-xxx and rerun in a terminal."
    fi
  fi

  if ! command -v codeops >/dev/null 2>&1; then
    local rc_file=""
    rc_file="$(detect_shell_rc)"
    warn "codeops is not on PATH for this shell session."
    warn "Adding '$CODEOPS_BIN_DIR' to PATH in: $rc_file"
    append_path_once "$rc_file" "$CODEOPS_BIN_DIR"
    log ""
    log "Open a new terminal, or run:"
    log "  source \"$rc_file\""
  fi

  log ""
  log "Done."
  log "Try:"
  log "  codeops --help"
  log "  codeops auth login"
  log ""
  log "Optional: configure global auth during install:"
  log "  CODEOPS_CONFIGURE=1 CODEOPS_MODEL=ep-xxxx curl -fsSL https://raw.githubusercontent.com/$CODEOPS_REPO/$CODEOPS_REF/install.sh | bash"
}

main "$@"
