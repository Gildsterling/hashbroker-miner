import tempfile
import unittest
from pathlib import Path

from protocol import CONTRACT, leading_zero_bits, proof_hash
from signer import minted_to_wallet, prepare_transaction, save_json, unprocessed_candidates, validate_candidate

WALLET = "0x" + "12" * 20
CHALLENGE = "0x" + "34" * 32


def candidate():
    nonce = next(n for n in range(100) if leading_zero_bits(proof_hash(WALLET, n, CHALLENGE)) >= 1)
    return {"wallet": WALLET, "nonce": nonce, "challenge": CHALLENGE,
            "hash": "0x" + proof_hash(WALLET, nonce, CHALLENGE).hex()}


class FakeRPC:
    def call(self, method, params):
        answers = {"eth_chainId": hex(4663), "eth_estimateGas": hex(110_000),
                   "eth_getBlockByNumber": {"baseFeePerGas": hex(10)},
                   "eth_maxPriorityFeePerGas": hex(2),
                   "eth_getBalance": hex(10**18), "eth_getTransactionCount": "0x3"}
        return answers[method]


class SignerTests(unittest.TestCase):
    def test_rejects_changed_challenge_and_insufficient_bits(self):
        state = {"challenge": "0x" + "55" * 32, "difficulty": 1}
        with self.assertRaisesRegex(ValueError, "stale"):
            validate_candidate(candidate(), WALLET, state)
        state["challenge"] = CHALLENGE
        state["difficulty"] = 256
        with self.assertRaisesRegex(ValueError, "difficulty"):
            validate_candidate(candidate(), WALLET, state)

    def test_total_includes_max_gas_and_signed_intent_has_no_from(self):
        state = {"challenge": CHALLENGE, "difficulty": 1, "supply": 0, "price": 100}
        config = {"wallet1_address": WALLET, "max_total_wei": 100 + 137_500 * 22}
        tx = prepare_transaction(FakeRPC(), config, state, candidate())
        self.assertEqual(tx["gas"], 137_500)
        self.assertEqual(tx["maxFeePerGas"], 22)
        self.assertNotIn("from", tx)
        config["max_total_wei"] -= 1
        with self.assertRaisesRegex(ValueError, "exceeds cap"):
            prepare_transaction(FakeRPC(), config, state, candidate())

    def test_pending_candidates_survive_a_poll_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidates" / "abc.json"
            save_json(path, candidate())
            self.assertEqual(len(list(unprocessed_candidates(Path(directory)))), 1)
            save_json(path.with_suffix(".result.json"), {"status": "submitted"})
            self.assertEqual(len(list(unprocessed_candidates(Path(directory)))), 0)

    def test_transfer_must_mint_to_wallet(self):
        receipt = {"logs": [{"address": CONTRACT, "topics": [
            "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
            "0x" + "0" * 64, "0x" + WALLET[2:].lower().zfill(64), "0x" + "0" * 64]}]}
        self.assertTrue(minted_to_wallet(receipt, WALLET))
        receipt["logs"][0]["topics"][2] = "0x" + "1" * 64
        self.assertFalse(minted_to_wallet(receipt, WALLET))


if __name__ == "__main__":
    unittest.main()
