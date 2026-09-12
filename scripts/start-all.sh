#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 PUBLIC_WALLET GPU_COUNT STREAM_BASE" >&2
  exit 2
fi

wallet=$1
gpus=$2
stream_base=$3
if [[ ! "$gpus" =~ ^[0-9]+$ || ! "$stream_base" =~ ^[0-9]+$ ]] ||
   (( gpus < 1 || gpus > 32 || stream_base < 1 || stream_base + gpus > 65536 )); then
  echo "invalid GPU count or stream partition" >&2
  exit 2
fi

cd /opt/hashbroker
python_bin=${HASHBROKER_PYTHON:-/opt/hashbroker/venv/bin/python}
if [[ ! -x "$python_bin" ]]; then
  echo "missing Python environment: $python_bin" >&2
  exit 2
fi
for ((gpu=0; gpu<gpus; gpu++)); do
  pidfile="miner-gpu${gpu}.pid"
  if [[ -f "$pidfile" ]] && kill -0 "$(< "$pidfile")" 2>/dev/null; then
    echo "GPU $gpu already running"
    continue
  fi
  CUDA_VISIBLE_DEVICES="$gpu" nohup "$python_bin" -u miner.py \
    --wallet "$wallet" --gpu 0 --stream-id "$((stream_base + gpu))" \
    --job-file /opt/hashbroker/job.json \
    --output "/opt/hashbroker/solution-gpu${gpu}.json" \
    > "miner-gpu${gpu}.log" 2>&1 &
  echo "$!" > "$pidfile"
done
