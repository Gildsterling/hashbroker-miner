# Setup

## Worker host (rented GPU box)

`scripts/provision-worker.sh HOST PORT SSH_KEYFILE GPUS STREAM_BASE WALLET
[CONTROLLER_PUBKEY_FILE]` does the whole worker side: it installs `uv` if the
image lacks it, creates `/opt/hashbroker/venv`, installs `cupy-cuda12x` and
`numpy` matched to the CUDA runtime the image already ships, copies
`miner.py`, `protocol.py` and `start-all.sh`, optionally installs the
controller's public key, and starts one process per GPU.

Requirements on the host: an NVIDIA driver, `python3`, outbound network access
for the package install, and a writable `/opt`. The worker needs no wallet key,
no RPC credential, and no inbound port.

Check the host before trusting it:

```bash
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader
```

For images whose `python3` is old or lacks `venv`, install `uv` first and let it
provide the interpreter. A CUDA 12 build of CuPy works on newer drivers too,
because the wheel carries its own runtime.

## Controller

1. Create a Python environment and install `requests` and `eth-account`.
2. Put the controller files somewhere private, for example
   `/opt/hashbroker-controller`, and keep runtime state beside them.
3. Copy `config.example.json` to an ignored `config.json` (mode 0600) and fill
   it in. `wallet1_provider` accepts:
   - `key_file`: a mode-0600 file holding the private key
   - `nft_bot_wallet_manager`: decrypt wallet `"1"` from a local encrypted
     wallet manager; set `wallet1_manager_root` to that manager's directory,
     which must also hold its `.env`
4. Pin the rental host keys into `known_hosts` (`ssh-keyscan -p PORT HOST`) and
   point both the SSH key and the file at private paths. The signer never
   disables host-key checking.
5. `python3 signer.py --config config.json --check` — reads local credentials
   and the chain, prints supply/difficulty/price/cap, and signs nothing.
6. Start the signer under a process manager, for example:

```ini
[Unit]
Description=HashBroker signer
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/hashbroker-controller
ExecStart=/usr/bin/python3 /opt/hashbroker-controller/signer.py --config /opt/hashbroker-controller/config.json
Restart=on-failure
RestartSec=2
UMask=0077

[Install]
WantedBy=multi-user.target
```

## Dashboard

`python3 dashboard.py --runtime PRIVATE_RUNTIME_DIR` (add `--once` for a single
snapshot). It only reads the runtime directory, so it can run over SSH in a
terminal, and closing it never stops mining. If the panel shows zero GPUs while
the workers are alive, the status files are simply older than the freshness
window — check that the signer's poll loop is completing, not just that the
process is up.

## Health checklist

- `pgrep -c -f "[m]iner.py"` equals the GPU count on each host
- `nvidia-smi --query-compute-apps=pid --format=csv,noheader | wc -l` matches,
  proving no two workers share one GPU
- `job.json` modification time is a second or two old
- panel freshness is under a few seconds and reported GH/s matches
  `per-GPU GH/s × GPU count`
- best observed leading-zero bits sit near `log2(hashes tried per challenge
  window)`; far below that means the worker is not really hashing
