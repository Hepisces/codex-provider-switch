#!/bin/zsh
set -eu
SCRIPT_DIR="${0:A:h}"
PYTHON_BIN=""
CANDIDATES=(
  "${HOME}/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
  python3 python3.14 python3.13 python3.12 python3.11
  /opt/homebrew/bin/python3 /usr/local/bin/python3
)
for CANDIDATE in "${CANDIDATES[@]}"; do
  if command -v "$CANDIDATE" >/dev/null 2>&1 && "$CANDIDATE" -c 'import tomllib' >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v "$CANDIDATE")"
    break
  fi
done
if [[ -z "$PYTHON_BIN" ]]; then
  print '需要 Python 3.11 或更新版本（含 tomllib）。请先安装，再运行本工具。'
  if (( $# == 0 )); then
    read '?按 Enter 关闭。'
  fi
  exit 1
fi
if (( $# )); then
  exec "$PYTHON_BIN" "$SCRIPT_DIR/codex_provider_switch.py" "$@"
fi
set +e
"$PYTHON_BIN" "$SCRIPT_DIR/codex_provider_switch.py"
RESULT=$?
read '?按 Enter 关闭。'
exit "$RESULT"
