# Architecture

One private controller, many untrusted workers. The split exists so that a
compromised rental (shared GPU marketplaces hand out root on a container, not a
dedicated box) cannot spend funds or leak keys: workers receive a job and
return an unsigned proof.

## Job flow

The controller reads `challenge()`, `currentDifficulty()`, `mintPrice()` and
`totalSupply()` in one batched RPC call, then writes a small `job.json` to every
rental over SSH:

```json
{"wallet": "0x...", "challenge": "0x...", "difficulty": 47, "updated_at": 1789230000}
```

Workers ignore a job older than five seconds, so a controller outage stops
mining instead of letting workers grind a dead challenge. Batch RPC plus SSH
`ControlMaster` reuse keep the publish/poll loop near one second; without
connection reuse the loop degrades to tens of seconds and candidates are
harvested late, which directly costs mints.

## Candidate flow

1. A GPU writes `solution-gpuN.json` only after a hit, and does not start
   another nonce range until that file disappears.
2. The controller pulls the file, stores the exact bytes under a content hash,
   and only then acknowledges by archiving the remote file. Persist-before-ack
   is what makes a controller crash recoverable.
3. The controller re-derives the digest on CPU and re-reads the chain: if the
   challenge moved, the proof is worthless and is marked rejected rather than
   retried forever.
4. Surviving candidates are sized with `eth_estimateGas`, priced from the block
   base fee plus a priority fee, checked against the cap, signed, and
   broadcast. An unresolved transaction is reconciled before a new one is
   signed, so a crash cannot silently burn a nonce with two different intents.

Every GPU must carry a unique stream id: the nonce is partitioned as
`stream_id(16 bits) | segment(16 bits) | counter(32 bits)`. Overlapping
partitions waste the fleet's most expensive resource, so the signer refuses to
start with duplicate ranges.

## Failure modes worth designing against

- challenge rotated between hit and broadcast: expected occasionally; the
  signer must reject it cheaply, never block the queue
- RPC rate limiting (`HTTP 429`) or a lagging node: fail over between
  endpoints, and prefer a paid endpoint for reads and broadcast
- one worker pinned to the wrong GPU (for example a restart without
  `CUDA_VISIBLE_DEVICES`): two processes share one card and one card idles,
  which looks like a mysterious slowdown; verify with
  `nvidia-smi --query-compute-apps=pid --format=csv,noheader | wc -l`
- stale `job.json` because the controller died: workers must go idle
- an in-flight transaction after a restart: reconcile the receipt first
