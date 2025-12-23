#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_DIR="$PROJECT_ROOT/env"
PATTERN="${1:-.env.*}"

pids=()

cleanup() {
    if [ ${#pids[@]} -gt 0 ]; then
        echo "\n[run_all] 停止 edge 實例..."
        for pid in "${pids[@]}"; do
            kill "$pid" >/dev/null 2>&1 || true
        done
        wait "${pids[@]}" 2>/dev/null || true
    fi
}

trap cleanup SIGINT SIGTERM

shopt -s nullglob
files=("$ENV_DIR"/$PATTERN)
shopt -u nullglob

if [ ${#files[@]} -eq 0 ]; then
    echo "[run_all] 找不到符合 $ENV_DIR/$PATTERN 的 .env 檔案"
    exit 1
fi

for envfile in "${files[@]}"; do
    echo "[run_all] 啟動 $(basename "$envfile")"
    (
        set -a
        source "$envfile"
        set +a
        cd "$PROJECT_ROOT"
        exec python main.py
    ) &
    pids+=($!)
done

wait "${pids[@]}"
