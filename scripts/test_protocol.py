import hashlib
import unittest

from protocol import (block2_schedule, checked_nonce, job_id, leading_zero_bits,
                      mine_calldata, proof_hash, reference_split_hash)


class ProtocolTests(unittest.TestCase):
    def test_gpu_block_split_matches_sha256(self):
        challenge = "0x" + "a3" * 32
        self.assertEqual(len(block2_schedule(challenge)), 64)
        for nonce in (0, 1, 2**32, 2**64 - 1, 0x123456789ABCDEF0):
            wallet = "0x" + "%040x" % (nonce % (2**160))
            self.assertEqual(reference_split_hash(wallet, nonce, challenge),
                             proof_hash(wallet, nonce, challenge))

    def test_hash_is_packed_address_uint256_challenge(self):
        wallet = "0x" + "12" * 20
        challenge = "0x" + "34" * 32
        nonce = 0x123456789ABCDEF0
        material = bytes.fromhex("12" * 20) + nonce.to_bytes(32, "big") + bytes.fromhex("34" * 32)
        self.assertEqual(proof_hash(wallet, nonce, challenge), hashlib.sha256(material).digest())
        self.assertEqual(len(mine_calldata(nonce, challenge)), 2 + 8 + 64 + 64)

    def test_leading_bits_and_validation(self):
        self.assertEqual(leading_zero_bits(bytes.fromhex("0008") + bytes(30)), 12)
        self.assertEqual(leading_zero_bits(bytes(32)), 256)
        self.assertEqual(checked_nonce(2**64 - 1), 2**64 - 1)
        for bad in (-1, 2**64, "7", True):
            with self.assertRaises(ValueError):
                checked_nonce(bad)
        with self.assertRaises(ValueError):
            proof_hash("0x1234", 0, "0x" + "00" * 32)
        with self.assertRaises(ValueError):
            mine_calldata(-1, "0x" + "00" * 32)
        with self.assertRaises(ValueError):
            job_id("0x" + "00" * 32, 257)


if __name__ == "__main__":
    unittest.main()
