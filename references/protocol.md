# HashBroker protocol

Chain: Robinhood Chain (chain id `4663`). Contract:
`0x4272D6f51771839F596082eF48fa84D35239Bab3`. Supply cap 4444.

## Proof

```
digest = SHA256( address[20] || uint256 nonce[32, big-endian] || challenge[32] )
accept when leading_zero_bits(digest) >= currentDifficulty()
```

The official site's WebGPU shader splits the same message into two 64-byte
SHA-256 blocks: the first holds the address, 24 zero bytes and the low 8 bytes
of the nonce plus the first 12 challenge bytes; the second holds the remaining
20 challenge bytes, the `0x80` pad and length 672. Only a 64-bit nonce is
searched, so this skill rejects nonces outside `2^64`. `protocol.py` proves the
split equals `hashlib.sha256` in `reference_split_hash`, and the GPU re-checks
every candidate on the CPU before writing it.

## Contract calls

| purpose | selector | notes |
|---|---|---|
| `challenge()` | `0xd2ef7398` | bytes32, rotates on every mint |
| `currentDifficulty()` | `0x5c062d6c` | leading zero bits |
| `mintPrice()` | `0x6817c76c` | wei, charged as `msg.value` |
| `totalSupply()` | `0x18160ddd` | minted so far |
| `mine(uint256,bytes32)` | `0xe43e322c` | calldata = nonce ‖ challenge |

Pricing is stepped: #1–100 free, then 0.0001 ETH rising 0.0001 ETH every 200
paid mints. Read the live value instead of hardcoding an epoch table.

## How this was verified

Blockscout exposes the contract bytecode but not verified Solidity. The layout
above was confirmed by pulling real accepted `mine` transactions and
recomputing their digests off-chain: accepted proofs showed 48/45/45 leading
zero bits under this exact formula, and a synthetic non-qualifying call reverts
with the contract's custom error rather than a decode failure. Treat that as
strong behavioural evidence, not a source audit — re-verify if the contract is
redeployed or the address changes.
