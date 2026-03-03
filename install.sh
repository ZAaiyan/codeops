#!/usr/bin/env bash
set -euo pipefail

CODEOPS_REPO_DEFAULT="YOUR_GITHUB_USERNAME/codeops"
CODEOPS_REPO="${CODEOPS_REPO:-$CODEOPS_REPO_DEFAULT}"
CODEOPS_REPO_URL_DEFAULT="https://github.com/$CODEOPS_REPO"
CODEOPS_REPO_URL="${CODEOPS_REPO_URL:-$CODEOPS_REPO_URL_DEFAULT}"
CODEOPS_REF="${CODEOPS_REF:-main}"
CODEOPS_HOME="${CODEOPS_HOME:-$HOME/.local/codeops}"
CODEOPS_BIN_DIR="${CODEOPS_BIN_DIR:-$HOME/.local/bin}"

log() { printf '%s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing dependency: $1"
}

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

ensure_python_version() {
  python3 - <<'PY'
import sys
major, minor = sys.version_info[:2]
if (major, minor) < (3, 11):
    raise SystemExit(f"Python >= 3.11 required, got {sys.version.split()[0]}")
print(sys.version.split()[0])
PY
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
  need_cmd python3

  log "Installing codeops..."
  log "Repo: $CODEOPS_REPO_URL (ref: $CODEOPS_REF)"

  local pyver=""
  pyver="$(ensure_python_version)" || die "python3 version check failed"
  log "Python: $pyver"

  mkdir -p "$CODEOPS_HOME"
  mkdir -p "$CODEOPS_BIN_DIR"

  local venv_dir="$CODEOPS_HOME/venv"
  if [ ! -x "$venv_dir/bin/python" ]; then
    log "Creating venv: $venv_dir"
    python3 -m venv "$venv_dir"
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
}

main "$@"
