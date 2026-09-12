---
name: hashbroker-miner
description: Deploy and operate a HashBroker-style GPU proof-of-work miner that mines and mints NFTs on Robinhood Chain (SHA-256 proofs, mine(uint256,bytes32)). Use when the user wants to mine HashBroker, add or remove rented GPU hosts, recover found proofs, raise or lower a mint price cap, read the mining dashboard, or build any similar mine-to-mint fleet (use this skill as the reference template even when the token contract or hash function differs).
---

# HashBroker Miner

This skill runs a rented-GPU mining fleet that finds SHA-256 proofs below an
on-chain difficulty and mints one NFT per proof to a single wallet.

The layout follows a proven template: **a private controller signs, rented
boxes only compute.** Workers never see a wallet key or an RPC credential, so a
compromised rental can waste hashrate but cannot spend funds. Read
[references/architecture.md](references/architecture.md) before changing the job
or candidate pipeline, and [references/protocol.md](references/protocol.md)
before touching hashing, calldata, or validation.

## Mental model

The proof is `SHA256(address[20] || uint256 nonce || challenge[32])`, and the
contract accepts it when the digest has at least `currentDifficulty()` leading
zero bits. The nonce searched is 64-bit. `challenge()` changes as soon as
anyone mints, which invalidates every proof in flight — so a mint is a race, and
throughput only matters while the challenge is current.

Expected output is therefore **our hashrate share × the global mint rate**, not
`2^difficulty / hashrate`. Estimate both from the chain before promising a rate:
count `Transfer(0x0 → *)` logs for the contract to get the global mint rate, and
derive our share from the fleet's measured GH/s. Report the result as an average
with honest variance; a dry spell of a few multiples of the mean is normal.

## Configuration

Keep every secret outside any repository, in files the skill only reads:

- controller config: copy `scripts/config.example.json` to a private path and set
  the wallet address, a wallet key source, an explicit per-mint cap, the RPC
  endpoints, the SSH key and pinned `known_hosts`, a private runtime directory,
  and the `rentals` list
- wallet key: either a mode-0600 key file, or the `nft_bot_wallet_manager`
  provider that decrypts from the local encrypted manager
- never commit, print, or pass wallet keys, RPC keys, or rental passwords

The cap is the ceiling for **one** mint including the worst-case gas:
`value + gas × maxFeePerGas`. The signer refuses to sign, and stops instead of
overspending, when the live price reaches it. Set it deliberately; a value the
user has not approved is a spending decision, not a default.

## Operating workflow

1. Read live state before touching anything: `signer.py --check` prints the
   wallet, supply, difficulty, live price, and the configured cap without
   signing. Confirm the price is below the cap.
2. Provision each rented host with `scripts/provision-worker.sh`, which installs
   a CUDA Python environment, copies the worker files, authorizes the
   controller's key, and starts one worker per GPU with a unique stream id.
   Give every GPU in the fleet a distinct stream id so no two workers search the
   same nonces.
3. Start the signer on the controller (`signer.py --config ...`). It publishes
   fresh jobs, harvests unsigned candidates, revalidates them locally against
   the live chain, signs, broadcasts, and confirms the ERC-721 transfer.
4. Start the dashboard in a separate process: `dashboard.py --runtime DIR`.
   Closing it must never stop workers or the signer.
5. Verify, don't assume: a mint counts only after the receipt shows a
   `Transfer(0x0 → wallet)` for the contract, and the on-chain `balanceOf`
   increases. "Transaction prepared" and "broadcast" are not confirmations.

## Adding and removing capacity

Appending a rental to `rentals` with a fresh, non-overlapping `stream_base` is
the whole change; the signer validates that partitions do not overlap and can be
restarted while workers keep running. Removing a host means stopping its workers
and deleting its entry. Rented-hourly cost, not hashrate, sets profitability, so
when the user asks about adding cards, compute the marginal value of one GPU
(`share gain × global mint rate × mint price`) and compare it with the hourly
price before recommending anything.

## Tuning

The bundled kernel is already at the practical limit for this algorithm: batch
shape, register caps, and 2-way ILP were all measured and made no meaningful
difference, and consumer cards were power-limited at their maximum. Do not
promise gains from kernel micro-tuning. If throughput disappoints, measure
per-GPU GH/s with `scripts/bench.py`, compare against the expected
INT32-bound rate, and look at clocks, power limits, and whether any two
processes share one GPU.

## Safety

The signer spends real ETH. Verify `--check` output, keep the cap conservative,
confirm the wallet balance, and treat the contract's unverified source as a
risk to state plainly: proof layout and difficulty semantics here were
validated by reproducing real accepted `mine` transactions on-chain, which is
strong evidence but not a source-level audit.
