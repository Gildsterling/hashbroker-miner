#!/usr/bin/env python3
"""CUDA worker: public jobs in, unsigned proofs out. No wallet key or RPC token."""

import argparse
import json
import os
import time
from pathlib import Path

import cupy as cp
import numpy as np

from protocol import block2_schedule, hex_bytes, job_id, leading_zero_bits, proof_hash

CUDA = r'''
typedef unsigned int u32;
__device__ __constant__ u32 K[64] = {
  0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
  0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
  0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
  0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
  0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
  0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
  0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
  0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
};
__device__ __forceinline__ u32 rotr(u32 x, int n) { return (x >> n) | (x << (32-n)); }
__device__ __forceinline__ u32 s0(u32 x) { return rotr(x,7)^rotr(x,18)^(x>>3); }
__device__ __forceinline__ u32 s1(u32 x) { return rotr(x,17)^rotr(x,19)^(x>>10); }
__device__ __forceinline__ u32 S0(u32 x) { return rotr(x,2)^rotr(x,13)^rotr(x,22); }
__device__ __forceinline__ u32 S1(u32 x) { return rotr(x,6)^rotr(x,11)^rotr(x,25); }

// First block: 16 message words with a rolling schedule (16 registers, no spills).
__device__ __forceinline__ void compress1(const u32 *msg, u32 st[8]) {
    u32 w[16];
    #pragma unroll
    for (int i = 0; i < 16; ++i) w[i] = msg[i];
    u32 a=st[0], b=st[1], c=st[2], d=st[3], e=st[4], f=st[5], g=st[6], h=st[7];
    #pragma unroll
    for (int i = 0; i < 64; ++i) {
        u32 wi;
        if (i < 16) wi = w[i];
        else {
            wi = s1(w[(i+14)&15]) + w[(i+9)&15] + s0(w[(i+1)&15]) + w[i&15];
            w[i&15] = wi;
        }
        u32 t1 = h + S1(e) + ((e & f) ^ (~e & g)) + K[i] + wi;
        u32 t2 = S0(a) + ((a & b) ^ (a & c) ^ (b & c));
        h=g; g=f; f=e; e=d+t1; d=c; c=b; b=a; a=t1+t2;
    }
    st[0]+=a; st[1]+=b; st[2]+=c; st[3]+=d;
    st[4]+=e; st[5]+=f; st[6]+=g; st[7]+=h;
}

// Second block: its schedule is constant for the whole job, precomputed on the host.
__device__ __forceinline__ void compress2(const u32 *w2, u32 st[8]) {
    u32 a=st[0], b=st[1], c=st[2], d=st[3], e=st[4], f=st[5], g=st[6], h=st[7];
    #pragma unroll
    for (int i = 0; i < 64; ++i) {
        u32 t1 = h + S1(e) + ((e & f) ^ (~e & g)) + K[i] + w2[i];
        u32 t2 = S0(a) + ((a & b) ^ (a & c) ^ (b & c));
        h=g; g=f; f=e; e=d+t1; d=c; c=b; b=a; a=t1+t2;
    }
    st[0]+=a; st[1]+=b; st[2]+=c; st[3]+=d;
    st[4]+=e; st[5]+=f; st[6]+=g; st[7]+=h;
}

__device__ __forceinline__ int zero_bits(const u32 h[8]) {
    int bits = 0;
    #pragma unroll
    for (int i = 0; i < 8; ++i) {
        if (h[i]) return bits + __clz(h[i]);
        bits += 32;
    }
    return bits;
}

extern "C" __global__ void hash_one(const u32 *base, const u32 *w2, u32 hi, u32 lo, u32 *out) {
    if (blockIdx.x || threadIdx.x) return;
    u32 msg[16];
    #pragma unroll
    for (int i = 0; i < 5; ++i) msg[i] = base[i];
    #pragma unroll
    for (int i = 5; i < 11; ++i) msg[i] = 0;
    msg[11] = hi; msg[12] = lo;
    msg[13] = base[5]; msg[14] = base[6]; msg[15] = base[7];
    u32 st[8] = {0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
                 0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    compress1(msg, st);
    compress2(w2, st);
    #pragma unroll
    for (int i = 0; i < 8; ++i) out[i] = st[i];
}

// result = [found, nonceLo, nonceHi, bestBits]
extern "C" __global__ void mine_batch(const u32 *base, const u32 *w2, u32 hi, u32 low,
                                      u32 iterations, u32 difficulty, u32 *result) {
    u32 tid = blockIdx.x * blockDim.x + threadIdx.x;
    u32 first = low + tid * iterations;
    u32 msg[16];
    #pragma unroll
    for (int i = 0; i < 5; ++i) msg[i] = base[i];
    #pragma unroll
    for (int i = 5; i < 11; ++i) msg[i] = 0;
    msg[11] = hi;
    msg[13] = base[5]; msg[14] = base[6]; msg[15] = base[7];
    int best = 0;
    for (u32 i = 0; i < iterations; ++i) {
        if ((i & 7u) == 0u && *(volatile u32 *)result != 0u) break;
        u32 nonce = first + i;
        msg[12] = nonce;
        u32 st[8] = {0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
                     0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
        compress1(msg, st);
        compress2(w2, st);
        int bits = zero_bits(st);
        if (bits > best) best = bits;
        if (bits >= (int)difficulty) {
            if (atomicCAS(&result[0], 0u, 1u) == 0u) { result[1] = nonce; result[2] = hi; }
            break;
        }
    }
    if (best > 0) atomicMax(&result[3], (u32)best);
}
'''


def base_words(wallet: str, challenge: str) -> np.ndarray:
    raw = hex_bytes(wallet, 20) + hex_bytes(challenge, 32)
    return np.frombuffer(raw, dtype=">u4").astype(np.uint32)


def atomic_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def read_job(path: Path, wallet: str) -> dict:
    job = json.loads(path.read_text(encoding="utf-8"))
    if job["wallet"].lower() != wallet.lower():
        raise ValueError("job wallet mismatch")
    job_id(job["challenge"], job["difficulty"])
    if time.time() - job["updated_at"] > 5 or job["updated_at"] > time.time() + 2:
        raise ValueError("stale job")
    return job


def run(args) -> None:
    wallet = args.wallet
    hex_bytes(wallet, 20)
    if not 1 <= args.stream_id <= 65535:
        raise ValueError("stream-id must be 1..65535")
    batch_size = args.blocks * args.threads * args.iterations
    if min(args.blocks, args.threads, args.iterations) < 1 or batch_size > 2**32:
        raise ValueError("invalid batch size")
    job_path, output = Path(args.job_file), Path(args.output)
    status_path = output.with_suffix(".status.json")
    with cp.cuda.Device(args.gpu):
        module = cp.RawModule(code=CUDA, options=("--std=c++11",))
        one, batch = module.get_function("hash_one"), module.get_function("mine_batch")
        base_gpu = cp.zeros(13, dtype=cp.uint32)
        w2_gpu = cp.zeros(64, dtype=cp.uint32)
        result = cp.zeros(4, dtype=cp.uint32)
        test_out = cp.zeros(8, dtype=cp.uint32)
        current, low, segment, hashes, best = None, 0, 0, 0, 0
        last_status = 0.0
        while True:
            try:
                job = read_job(job_path, wallet)
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                time.sleep(0.2)
                continue
            identity = job_id(job["challenge"], job["difficulty"])
            if identity != current:
                current, low, segment, hashes, best = identity, 0, 0, 0, 0
                words = base_words(wallet, job["challenge"])
                base_gpu.set(words)
                w2_gpu.set(np.array(block2_schedule(job["challenge"]), dtype=np.uint32))
                nonce = (args.stream_id << 48) | 0x123456789ABC
                hi, lo = nonce >> 32, nonce & 0xffffffff
                one((1,), (1,), (base_gpu, w2_gpu, np.uint32(hi), np.uint32(lo), test_out))
                expected = proof_hash(wallet, nonce, job["challenge"])
                actual = b"".join(int(word).to_bytes(4, "big") for word in cp.asnumpy(test_out))
                if actual != expected:
                    raise RuntimeError("CUDA SHA-256 self-test disagrees with CPU")
                print(json.dumps({"kind": "job", "gpu": args.gpu, "challenge": job["challenge"]}), flush=True)
            if output.exists():
                time.sleep(0.1)
                continue
            if low + batch_size > 2**32:
                low = 0
                segment += 1
                if segment >= 2**16:
                    raise RuntimeError("nonce partition exhausted")
            hi = (args.stream_id << 16) | segment
            result.fill(0)
            began = time.monotonic()
            batch((args.blocks,), (args.threads,),
                  (base_gpu, w2_gpu, np.uint32(hi), np.uint32(low), np.uint32(args.iterations),
                   np.uint32(job["difficulty"]), result))
            found, nonce_lo, nonce_hi, batch_best = map(int, cp.asnumpy(result))
            duration = max(time.monotonic() - began, 0.001)
            hashes += batch_size
            best = max(best, batch_best)
            low += batch_size
            if found:
                nonce = (nonce_hi << 32) | nonce_lo
                proof = proof_hash(wallet, nonce, job["challenge"])
                if leading_zero_bits(proof) >= job["difficulty"]:
                    atomic_json(output, {"wallet": wallet, "challenge": job["challenge"],
                                         "difficulty": job["difficulty"], "nonce": nonce,
                                         "hash": "0x" + proof.hex(), "gpu": args.gpu,
                                         "found_at": time.time()})
                else:
                    raise RuntimeError("GPU candidate failed independent CPU validation")
            now = time.monotonic()
            if now - last_status >= 2.0:
                last_status = now
                atomic_json(status_path,
                            {"gpu": args.gpu, "challenge": job["challenge"], "best_bits": best,
                             "hashes": hashes, "hashrate": round(batch_size / duration),
                             "updated_at": time.time()})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--job-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--gpu", type=int, required=True)
    parser.add_argument("--stream-id", type=int, required=True)
    parser.add_argument("--blocks", type=int, default=2048)
    parser.add_argument("--threads", type=int, default=256)
    parser.add_argument("--iterations", type=int, default=32)
    run(parser.parse_args())
