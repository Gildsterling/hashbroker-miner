#!/usr/bin/env python3
"""Offline kernel benchmark: pick the fastest batch shape for a given GPU."""

import argparse
import time

import cupy as cp
import numpy as np

from miner import CUDA, base_words
from protocol import block2_schedule

CHALLENGE = "0x" + "ab" * 32


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--difficulty", type=int, default=255)
    parser.add_argument("--wallet", default="0x" + "11" * 20,
                        help="any address; only the shape matters for benchmarking")
    args = parser.parse_args()

    with cp.cuda.Device(args.gpu):
        module = cp.RawModule(code=CUDA, options=("--std=c++11",))
        batch = module.get_function("mine_batch")
        words = base_words(args.wallet, CHALLENGE)
        base_gpu = cp.asarray(words)
        w2_gpu = cp.asarray(np.array(block2_schedule(CHALLENGE), dtype=np.uint32))
        result = cp.zeros(4, dtype=cp.uint32)
        names = cp.cuda.runtime.getDeviceProperties(args.gpu)["name"].decode()
        print(f"GPU {args.gpu}: {names}")
        print(f"{'blocks':>7} {'threads':>8} {'iters':>6} {'GH/s':>8}")
        for blocks, threads, iterations in (
            (2048, 256, 32), (2048, 256, 64), (2048, 256, 128),
            (4096, 256, 32), (4096, 256, 64), (1024, 512, 64),
            (8192, 128, 32), (4096, 128, 128),
        ):
            per_pass = blocks * threads * iterations
            batch((blocks,), (threads,),
                  (base_gpu, w2_gpu, np.uint32(1), np.uint32(0), np.uint32(iterations),
                   np.uint32(args.difficulty), result))
            cp.cuda.runtime.deviceSynchronize()
            began = time.monotonic()
            for _ in range(args.rounds):
                result.fill(0)
                batch((blocks,), (threads,),
                      (base_gpu, w2_gpu, np.uint32(1), np.uint32(0), np.uint32(iterations),
                       np.uint32(args.difficulty), result))
            cp.cuda.runtime.deviceSynchronize()
            seconds = time.monotonic() - began
            print(f"{blocks:>7} {threads:>8} {iterations:>6} {per_pass * args.rounds / seconds / 1e9:>8.2f}")


if __name__ == "__main__":
    main()
