#!/usr/bin/env bash
# Install the worker on a fresh rented GPU host and start one process per GPU.
#
# Usage:
#   provision-worker.sh HOST PORT SSH_KEYFILE GPUS STREAM_BASE WALLET [AUTHORIZE_KEYFILE]
#
# AUTHORIZE_KEYFILE is the controller's *public* key; pass it to let the
# controller reach this host. Never pass a private key path or password here.
set -euo pipefail

if [[ $# -lt 6 || $# -gt 7 ]]; then
  sed -n '2,9p' "$0" >&2
  exit 2
fi

host=$1
port=$2
keyfile=$3
gpus=$4
stream_base=$5
wallet=$6
authorize_pub=${7:-}

[[ -r "$keyfile" ]] || { echo "missing SSH key file: $keyfile" >&2; exit 2; }
[[ "$gpus" =~ ^[0-9]+$ && "$stream_base" =~ ^[0-9]+$ ]] || { echo "GPUS and STREAM_BASE must be integers" >&2; exit 2; }
(( gpus >= 1 && gpus <= 32 && stream_base >= 1 && stream_base + gpus <= 65536 )) || { echo "invalid GPU count or stream range" >&2; exit 2; }
[[ "$wallet" =~ ^0x[0-9a-fA-F]{40}$ ]] || { echo "WALLET must be a 20-byte hex address" >&2; exit 2; }
if [[ -n "$authorize_pub" ]]; then
  [[ -r "$authorize_pub" ]] || { echo "missing public key file: $authorize_pub" >&2; exit 2; }
fi

here=$(cd "$(dirname "$0")" && pwd)
ssh_base=(ssh -p "$port" -i "$keyfile" -o BatchMode=yes -o IdentitiesOnly=yes
          -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "root@$host")
scp_base=(scp -P "$port" -i "$keyfile" -o BatchMode=yes -o IdentitiesOnly=yes
          -o StrictHostKeyChecking=accept-new)

echo "== installing CUDA python environment on $host:$port =="
"${ssh_base[@]}" 'set -e
  mkdir -p /opt/hashbroker
  if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 || true
  fi
  export PATH="$PATH:/root/.local/bin"
  if [ ! -x /opt/hashbroker/venv/bin/python ]; then
    command -v uv >/dev/null 2>&1 && uv venv --python 3 /opt/hashbroker/venv >/dev/null 2>&1 || true
  fi
  if [ ! -x /opt/hashbroker/venv/bin/python ]; then
    python3 -m venv /opt/hashbroker/venv
  fi
  /opt/hashbroker/venv/bin/python -V
  command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi --query-gpu=index,name --format=csv,noheader || echo "WARNING: no nvidia-smi"'

echo "== installing cupy and numpy =="
"${ssh_base[@]}" 'export PATH="$PATH:/root/.local/bin"
  uv pip install --python /opt/hashbroker/venv/bin/python cupy-cuda12x numpy 2>&1 | tail -3'

echo "== copying worker files =="
"${scp_base[@]}" "$here/miner.py" "$here/protocol.py" "$here/start-all.sh" "root@$host:/opt/hashbroker/"

if [[ -n "$authorize_pub" ]]; then
  echo "== authorizing the controller key =="
  key=$(tr -d '\n' < "$authorize_pub")
  "${ssh_base[@]}" "umask 077; mkdir -p /root/.ssh; touch /root/.ssh/authorized_keys
    chmod 700 /root/.ssh; chmod 600 /root/.ssh/authorized_keys
    grep -qxF '$key' /root/.ssh/authorized_keys || printf '%s\n' '$key' >> /root/.ssh/authorized_keys
    wc -l < /root/.ssh/authorized_keys"
fi

echo "== starting $gpus workers (streams $stream_base..$((stream_base + gpus - 1))) =="
"${ssh_base[@]}" "cd /opt/hashbroker && bash start-all.sh $wallet $gpus $stream_base >/tmp/hashbroker-start.log 2>&1
  sleep 12
  echo workers=\$(pgrep -c -f '[m]iner.py')
  tail -n 2 /opt/hashbroker/miner-gpu0.log 2>/dev/null || true"
