#!/usr/bin/env python3
"""Read-only local dashboard; does not control workers or signer."""

import argparse
import json
import os
import time
from pathlib import Path


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def render(runtime: Path) -> str:
    chain = read_json(runtime / "chain.json")
    workers = read_json(runtime / "workers.json")
    statuses = list(workers.get("status", {}).values())
    fresh = [row for row in statuses if time.time() - row.get("updated_at", 0) < 45]
    live = [row for row in statuses if time.time() - row.get("updated_at", 0) < 15]
    rate = sum(row.get("hashrate", 0) for row in fresh)
    candidates = [path for path in (runtime / "candidates").glob("*.json")
                  if not path.name.endswith(".result.json")] if (runtime / "candidates").exists() else []
    outcome_files = list((runtime / "candidates").glob("*.result.json")) if (runtime / "candidates").exists() else []
    outcomes = [read_json(path).get("status") for path in outcome_files]
    confirmed = list((runtime / "confirmed").glob("*.json")) if (runtime / "confirmed").exists() else []
    receipts = list((runtime / "receipts").glob("*.json")) if (runtime / "receipts").exists() else []
    pending = read_json(runtime / "pending.json")
    age = round(time.time() - chain.get("updated_at", 0)) if chain else None
    stale = "未知" if age is None else (f"{age}s 前" if age < 10 else f"过期 {age}s")
    elapsed = round(time.time() - chain["started_at"]) if chain.get("started_at") else "?"
    lines = ["HASHBROKER / 一号钱包  (只读)",
             f"运行: {elapsed}s  链上数据: {stale}  铸造: {chain.get('supply', '?')}/4444  难度: {chain.get('difficulty', '?')} bits",
             f"当前价格: {chain.get('price', '?')} wei  挑战: {str(chain.get('challenge', '?'))[:18]}...",
             f"钱包余额: {chain.get('balance_wei', '?')} wei  单张总费上限: {chain.get('max_total_wei', '?')} wei",
             f"GPU 在线: {len(live)}/{len(statuses)}  速率: {rate / 1e9:.2f} GH/s",
             f"候选: {len(outcome_files)} 已处理 / {len(candidates)} 持久化  "
             f"拒绝: {outcomes.count('rejected')}  交易已准备: {outcomes.count('prepared')}",
             f"确认 mint: {len(confirmed)}  失败回执: {len(receipts)-len(confirmed)}  待确认: {'是' if pending else '否'}"]
    if fresh:
        lines.append("GPU: " + "  ".join(f"{row.get('gpu','?')}:{row.get('best_bits','?')}bit"
                                       for row in sorted(fresh, key=lambda r: r.get("gpu", 0))))
    try:
        events = (runtime / "events.jsonl").read_text(encoding="utf-8").splitlines()[-5:]
        lines.append("最近事件:")
        for line in events:
            row = json.loads(line)
            lines.append(f"  {time.strftime('%H:%M:%S', time.localtime(row['at']))} {row['kind']}")
    except (OSError, ValueError, KeyError):
        pass
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        if not args.once:
            os.system("clear")
        print(render(args.runtime), flush=True)
        if args.once:
            break
        time.sleep(2)
