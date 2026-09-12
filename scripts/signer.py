#!/usr/bin/env python3
"""One-wallet HashBroker controller. Never runs unless explicitly started."""

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import time
from pathlib import Path

import requests
from eth_account import Account

from protocol import (CHAIN_ID, CONTRACT, MAX_SUPPLY, SELECTORS, checked_nonce, hex_bytes,
                      leading_zero_bits, mine_calldata, proof_hash)

REMOTE = "/opt/hashbroker"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def save_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def event(runtime: Path, kind: str, **fields) -> None:
    row = {"at": time.time(), "kind": kind, **fields}
    with (runtime / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    print(json.dumps(row, sort_keys=True), flush=True)


class RPCError(RuntimeError):
    """A valid JSON-RPC error response (for example a contract revert)."""


class RPC:
    """Batch-capable client that fails over between independent endpoints."""

    def __init__(self, urls):
        if isinstance(urls, str):
            urls = [urls]
        if not urls or any(not str(url).startswith("https://") for url in urls):
            raise ValueError("RPC endpoints must use HTTPS")
        self.urls = list(urls)
        self.session = requests.Session()
        self.sequence = 0
        self.cooldown: dict[str, float] = {}

    def _order(self):
        now = time.time()
        healthy = [url for url in self.urls if self.cooldown.get(url, 0.0) < now]
        return healthy or list(self.urls)

    def batch(self, requests_to_send):
        last: Exception | None = None
        for url in self._order():
            self.sequence += 1
            payload = [{"jsonrpc": "2.0", "id": index + 1, "method": method, "params": params}
                       for index, (method, params) in enumerate(requests_to_send)]
            try:
                response = self.session.post(url, json=payload, timeout=(3, 8))
                if response.status_code == 429:
                    self.cooldown[url] = time.time() + 30
                    last = RuntimeError("HTTP 429 from RPC endpoint")
                    continue
                response.raise_for_status()
                rows = {row.get("id"): row for row in response.json()}
                results = []
                for index, (method, _) in enumerate(requests_to_send):
                    row = rows.get(index + 1)
                    if row is None:
                        raise ValueError("RPC batch response is missing an entry")
                    if "error" in row:
                        raise RPCError(f"{method} rejected: {row['error'].get('code')}")
                    results.append(row["result"])
                return results
            except RPCError:
                raise
            except Exception as exc:
                self.cooldown[url] = time.time() + 10
                last = exc
        raise RuntimeError(f"all RPC endpoints failed: {last}")

    def call(self, method: str, params: list):
        return self.batch([(method, params)])[0]

    def contract_uint(self, name: str) -> int:
        return int(self.call("eth_call", [{"to": CONTRACT, "data": SELECTORS[name]}, "latest"]), 16)

    def state(self) -> dict:
        challenge, difficulty, price, supply = self.batch([
            ("eth_call", [{"to": CONTRACT, "data": SELECTORS[name]}, "latest"])
            for name in ("challenge", "difficulty", "price", "supply")
        ])
        return {"challenge": challenge, "difficulty": int(difficulty, 16),
                "price": int(price, 16), "supply": int(supply, 16)}


def load_config(path: Path) -> tuple[dict, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    wallet = data["wallet1_address"]
    hex_bytes(wallet, 20)
    if not isinstance(data["max_total_wei"], int) or data["max_total_wei"] <= 0:
        raise ValueError("set an explicit positive max_total_wei")
    provider = data.get("wallet1_provider", "key_file")
    if provider == "nft_bot_wallet_manager":
        # The manager's location is deployment-specific, so it lives in the
        # private config rather than in this file.
        root = Path(data["wallet1_manager_root"])
        if not root.is_dir():
            raise ValueError("wallet1_manager_root is not a directory")
        from dotenv import load_dotenv
        sys.path.insert(0, str(root))
        load_dotenv(root / ".env")
        from src.agent_nft_mint_bot.config import Settings
        from src.agent_nft_mint_bot.wallet_manager import WalletManager
        settings = Settings.from_env()
        manager = WalletManager(settings.db_path, settings.fernet_key)
        stored_wallet = manager.get_wallet("1")
        if stored_wallet.address.lower() != wallet.lower():
            raise ValueError("wallet manager wallet 1 address mismatch")
        account = Account.from_key(manager.decrypt_private_key("1"))
    elif provider == "key_file":
        keyfile = Path(data["wallet1_key_file"])
        mode = stat.S_IMODE(keyfile.stat().st_mode)
        if mode & 0o077:
            raise ValueError("wallet key file must not be readable by group or other")
        account = Account.from_key(keyfile.read_text(encoding="ascii").strip())
    else:
        raise ValueError("unknown wallet provider")
    if account.address.lower() != wallet.lower():
        raise ValueError("wallet 1 address does not match key file")
    if not data.get("rentals"):
        raise ValueError("configure at least one rental")
    streams = set()
    names = set()
    for rental in data["rentals"]:
        host, port = rental["host"], rental["port"]
        gpus, stream_base = rental["gpus"], rental["stream_base"]
        if not re.fullmatch(r"[A-Za-z0-9.-]+", host) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("invalid rental SSH host or port")
        if not isinstance(gpus, int) or not isinstance(stream_base, int) or not 1 <= gpus <= 32 or not 1 <= stream_base <= 65536 - gpus:
            raise ValueError("invalid GPU count or stream partition")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", rental["name"]) or rental["name"] in names:
            raise ValueError("invalid or duplicate rental name")
        names.add(rental["name"])
        partition = set(range(stream_base, stream_base + gpus))
        if partition & streams:
            raise ValueError("overlapping GPU nonce partitions")
        streams.update(partition)
    for name in ("ssh_key", "known_hosts"):
        if not Path(data[name]).is_file():
            raise ValueError(f"missing {name}")
    return data, account


def ssh(config: dict, rental: dict, command: str) -> str:
    result = subprocess.run([
        "ssh", "-i", config["ssh_key"], "-p", str(rental["port"]),
        "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={config['known_hosts']}", "-o", "ConnectTimeout=3",
        "-o", "ControlMaster=auto",
        "-o", f"ControlPath={config['runtime_dir']}/ssh-%r@%h:%p",
        "-o", "ControlPersist=180",
        "root@" + rental["host"], command,
    ], capture_output=True, text=True, timeout=12, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"SSH exited {result.returncode}")
    return result.stdout


def remote_python(config: dict, rental: dict, code: str, *args: str) -> str:
    return ssh(config, rental, "python3 -c " + shlex.quote(code) + " " + " ".join(shlex.quote(arg) for arg in args))


PUBLISH_CODE = """import base64, os, sys
path='/opt/hashbroker/job.json'
with open(path+'.tmp','wb') as f:
 f.write(base64.b64decode(sys.argv[1])); f.flush(); os.fsync(f.fileno())
os.replace(path+'.tmp',path)
"""
POLL_CODE = """import base64, glob, json, os
root='/opt/hashbroker/'
out={'candidates':{},'status':{}}
for path in glob.glob(root+'solution-gpu*.json'):
 name=os.path.basename(path)
 if name.endswith('.status.json'):
  try: out['status'][name]=json.load(open(path))
  except (OSError,ValueError): pass
 elif name[12:-5].isdigit():
  try: out['candidates'][name]=base64.b64encode(open(path,'rb').read()).decode()
  except OSError: pass
print(json.dumps(out,separators=(',',':')))
"""
ACK_CODE = """import hashlib, os, sys
name,digest=sys.argv[1:]
path='/opt/hashbroker/'+name
if hashlib.sha256(open(path,'rb').read()).hexdigest()!=digest: raise SystemExit(2)
os.makedirs('/opt/hashbroker/archive',exist_ok=True)
os.replace(path,'/opt/hashbroker/archive/'+digest+'.json')
"""


def publish(config: dict, rental: dict, state: dict) -> None:
    job = {"wallet": config["wallet1_address"], "challenge": state["challenge"],
           "difficulty": state["difficulty"], "updated_at": time.time()}
    payload = base64.b64encode(json.dumps(job, separators=(",", ":")).encode()).decode()
    remote_python(config, rental, PUBLISH_CODE, payload)


def ingest(config: dict, rental: dict, runtime: Path) -> dict:
    remote = json.loads(remote_python(config, rental, POLL_CODE))
    for name, encoded in remote["candidates"].items():
        if not re.fullmatch(r"solution-gpu\d+\.json", name):
            continue
        raw = base64.b64decode(encoded, validate=True)
        digest = hashlib.sha256(raw).hexdigest()
        path = runtime / "candidates" / f"{digest}.json"
        if not path.exists():
            candidate = json.loads(raw)
            save_bytes(path, raw)
            event(runtime, "candidate_received", digest=digest[:12], gpu=candidate.get("gpu"),
                  rental=rental["name"])
        remote_python(config, rental, ACK_CODE, name, digest)
    return {f"{rental['name']}/{name}": status for name, status in remote["status"].items()}


def validate_candidate(candidate: dict, wallet: str, state: dict) -> None:
    if candidate["wallet"].lower() != wallet.lower():
        raise ValueError("candidate wallet mismatch")
    if candidate["challenge"].lower() != state["challenge"].lower():
        raise ValueError("candidate challenge is stale")
    nonce = checked_nonce(candidate["nonce"])
    digest = proof_hash(wallet, nonce, state["challenge"])
    if candidate["hash"].lower() != "0x" + digest.hex():
        raise ValueError("candidate hash mismatch")
    if leading_zero_bits(digest) < state["difficulty"]:
        raise ValueError("candidate below current difficulty")


def prepare_transaction(rpc: RPC, config: dict, state: dict, candidate: dict) -> dict:
    wallet = config["wallet1_address"]
    validate_candidate(candidate, wallet, state)
    if state["supply"] >= MAX_SUPPLY:
        raise ValueError("collection sold out")
    price, cap = state["price"], config["max_total_wei"]
    if price < 0 or price >= cap:
        raise ValueError("mint price exceeds total fee cap")
    if int(rpc.call("eth_chainId", []), 16) != CHAIN_ID:
        raise RuntimeError("wrong RPC chain")
    data = mine_calldata(candidate["nonce"], state["challenge"])
    gas_estimate = int(rpc.call("eth_estimateGas", [{"from": wallet, "to": CONTRACT,
                                                      "value": hex(price), "data": data}]), 16)
    gas = max(100_000, (gas_estimate * 125 + 99) // 100)
    latest = rpc.call("eth_getBlockByNumber", ["latest", False])
    base_fee = int(latest.get("baseFeePerGas") or "0x0", 16)
    priority = int(rpc.call("eth_maxPriorityFeePerGas", []), 16)
    max_fee = 2 * base_fee + priority
    if max_fee <= 0 or base_fee < 0 or priority < 0:
        raise RuntimeError("invalid gas quote")
    total = price + gas * max_fee
    if total > cap:
        raise ValueError("mint plus maximum gas exceeds cap")
    if int(rpc.call("eth_getBalance", [wallet, "latest"]), 16) < total:
        raise ValueError("wallet balance insufficient")
    return {"chainId": CHAIN_ID, "to": CONTRACT, "value": price,
            "data": data, "gas": gas, "maxPriorityFeePerGas": priority,
            "maxFeePerGas": max_fee, "nonce": int(rpc.call("eth_getTransactionCount", [wallet, "pending"]), 16),
            "type": 2}


def minted_to_wallet(receipt: dict, wallet: str) -> bool:
    recipient = "0x" + wallet[2:].lower().zfill(64)
    return any(log["address"].lower() == CONTRACT.lower() and len(log["topics"]) >= 4
               and log["topics"][0].lower() == TRANSFER_TOPIC
               and log["topics"][1] == "0x" + "0" * 64
               and log["topics"][2].lower() == recipient for log in receipt.get("logs", []))


def reconcile(rpc: RPC, config: dict, account, runtime: Path) -> bool:
    path = runtime / "pending.json"
    if not path.exists():
        return False
    pending = json.loads(path.read_text(encoding="utf-8"))
    receipt = rpc.call("eth_getTransactionReceipt", [pending["hash"]])
    if receipt:
        ok = int(receipt["status"], 16) == 1 and minted_to_wallet(receipt, config["wallet1_address"])
        event(runtime, "mint_confirmed" if ok else "transaction_failed", hash=pending["hash"])
        save_json(runtime / "receipts" / (pending["hash"] + ".json"), receipt)
        if ok:
            save_json(runtime / "confirmed" / (pending["hash"] + ".json"),
                      {"hash": pending["hash"], "at": time.time()})
        path.unlink()
        return False
    signed = Account.sign_transaction(pending["tx"], account.key)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    if "0x" + bytes(signed.hash).hex() != pending["hash"].lower():
        raise RuntimeError("persisted transaction intent changed")
    if time.time() - pending.get("last_broadcast_at", 0) >= 5:
        pending["last_broadcast_at"] = time.time()
        save_json(path, pending)
        try:
            rpc.call("eth_sendRawTransaction", ["0x" + bytes(raw).hex()])
            event(runtime, "broadcast", hash=pending["hash"])
        except Exception:
            # An already-known transaction and an uncertain RPC response both remain pending.
            event(runtime, "broadcast_unconfirmed", hash=pending["hash"])
    return True


def submit(rpc: RPC, config: dict, account, runtime: Path, candidate: dict) -> None:
    state = rpc.state()
    transaction = prepare_transaction(rpc, config, state, candidate)
    signed = Account.sign_transaction(transaction, account.key)
    txhash = "0x" + bytes(signed.hash).hex()
    save_json(runtime / "pending.json", {"tx": transaction, "hash": txhash,
                                         "candidate": candidate["hash"], "at": time.time()})
    event(runtime, "transaction_prepared", hash=txhash, total_cap_wei=config["max_total_wei"])
    reconcile(rpc, config, account, runtime)


def unprocessed_candidates(runtime: Path):
    for path in sorted((runtime / "candidates").glob("*.json")):
        if path.name.endswith(".result.json") or path.with_suffix(".result.json").exists():
            continue
        try:
            yield path, json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            event(runtime, "candidate_corrupt", file=path.name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--check", action="store_true", help="read-only local and chain checks")
    args = parser.parse_args()
    config, account = load_config(args.config)
    rpc = RPC(config.get("rpc_urls") or config["rpc_url"])
    state = rpc.state()
    hex_bytes(state["challenge"], 32)
    if not 1 <= state["difficulty"] <= 256 or not 0 <= state["supply"] <= MAX_SUPPLY:
        raise RuntimeError("contract state outside expected bounds")
    if args.check:
        print(json.dumps({"wallet": account.address, "supply": state["supply"],
                          "difficulty": state["difficulty"], "price_wei": state["price"],
                          "max_total_wei": config["max_total_wei"]}))
        return
    runtime = Path(config["runtime_dir"])
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (runtime / "signer.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    previous = None
    started_at = time.time()
    while True:
        try:
            state = rpc.state()
            hex_bytes(state["challenge"], 32)
            if state["supply"] >= MAX_SUPPLY or state["price"] >= config["max_total_wei"]:
                event(runtime, "paused_limit", supply=state["supply"], price_wei=state["price"])
                time.sleep(10)
                continue
            balance = int(rpc.call("eth_getBalance", [config["wallet1_address"], "latest"]), 16)
            save_json(runtime / "chain.json", {**state, "balance_wei": balance,
                                                "max_total_wei": config["max_total_wei"],
                                                "started_at": started_at, "updated_at": time.time()})
            if state["challenge"] != previous:
                event(runtime, "challenge", challenge=state["challenge"], difficulty=state["difficulty"])
                previous = state["challenge"]
            statuses = {}
            for rental in config["rentals"]:
                try:
                    publish(config, rental, state)
                    statuses.update(ingest(config, rental, runtime))
                except Exception as exc:
                    event(runtime, "rental_error", rental=rental["name"], error=type(exc).__name__)
            save_json(runtime / "workers.json", {"updated_at": time.time(), "status": statuses})
            pending = reconcile(rpc, config, account, runtime)
            for path, candidate in unprocessed_candidates(runtime):
                result = path.with_suffix(".result.json")
                if result.exists() or pending:
                    continue
                try:
                    submit(rpc, config, account, runtime, candidate)
                    save_json(result, {"status": "prepared", "at": time.time()})
                    pending = True
                except ValueError as exc:
                    save_json(result, {"status": "rejected", "reason": str(exc), "at": time.time()})
                    event(runtime, "candidate_rejected", reason=str(exc))
                except RPCError as exc:
                    # A revert here means the proof is not acceptable right now
                    # (almost always a challenge that moved on); retrying it forever
                    # would block the queue.
                    save_json(result, {"status": "rejected", "reason": str(exc), "at": time.time()})
                    event(runtime, "candidate_reverted", reason=str(exc))
            time.sleep(0.5)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            event(runtime, "controller_error", error=type(exc).__name__, message=str(exc)[:120])
            time.sleep(2)


if __name__ == "__main__":
    main()
