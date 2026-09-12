# hashbroker-miner

An agent-readable skill and source for running a HashBroker-style GPU
proof-of-work miner that mines and mints NFTs on Robinhood Chain.

The design keeps trust boundaries strict: a private controller reads the chain,
publishes jobs, validates proofs, and signs transactions, while rented GPUs only
compute. A rented box never receives a wallet key or an RPC credential, so a
compromised host can waste hashrate but cannot spend funds.

## Install as a skill

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/Gildsterling/hashbroker-miner ~/.codex/skills/hashbroker-miner
```

Start a new agent task afterwards; the skill is discovered at task start. Any
agent that can read files can also use it directly by reading `SKILL.md` and
following the references.

## What is inside

- `SKILL.md` — when to use it, the proof-of-work mental model, the operating
  workflow, and the safety rules
- `references/protocol.md` — hash layout, contract selectors, pricing, and how
  the layout was verified against real accepted transactions
- `references/architecture.md` — job and candidate flow, failure modes
- `references/setup.md` — worker and controller setup, systemd unit, health
  checklist
- `scripts/` — CUDA worker, protocol layer, controller/signer, read-only
  dashboard, one-command worker provisioning, and unit tests

## Configuration

Nothing sensitive is stored here. Copy `scripts/config.example.json` to a
private path and fill in your own wallet, key source, RPC endpoints, SSH key,
pinned `known_hosts`, and `rentals`. Set `max_total_wei` deliberately: it is the
ceiling for one mint including worst-case gas, and the signer refuses to sign
above it.

Each fleet should use its own wallet. Do not share a wallet between operators.

## Verify before trusting

The contract's Solidity source is not verified on the block explorer. The proof
layout and difficulty semantics in this repository were validated by pulling
real accepted `mine` transactions and recomputing their digests off-chain, and
by confirming that a non-qualifying call reverts with the contract's custom
error. That is strong behavioural evidence, not a source audit. Re-verify if the
contract address changes.

Run the bundled tests before deploying:

```bash
cd scripts && python3 -m unittest discover -p 'test_*.py'
```

## Measured performance notes

On a fleet of 8x RTX 4090, 4x RTX 5090 and 4x RTX 4090, a kernel rewrite
(rolling 16-word schedule plus a host-precomputed constant tail-block schedule,
no per-hash atomics) took aggregate throughput from 32 GH/s to about 141 GH/s.
After that the kernel is saturated: batch shape, register caps, and two-way ILP
per thread all measured within a few percent, and the 4090s were already at
their 450 W limit. A 5090 is only about 1.2x a 4090 for SHA-256 because the
workload is INT32-throughput bound.

Expected output is `our hashrate share x the global mint rate`, not
`2^difficulty / hashrate`: the challenge rotates whenever anyone mints, which
invalidates proofs in flight.
