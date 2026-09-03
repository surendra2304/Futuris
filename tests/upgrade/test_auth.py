import unittest
from datetime import UTC, datetime, timedelta
from futuris.upgrade.auth import CredentialHasher, StoredCredential, parse_api_key_header, validate_production_credentials


class AuthTests(unittest.TestCase):
    def test_kdf_round_trip(self):
        hasher = CredentialHasher()
        secret = "futuris_" + "x" * 40
        salt, verifier = hasher.hash_secret(secret)
        stored = StoredCredential("c1", "p1", "t1", salt, verifier, hasher.iterations, datetime.now(UTC))
        self.assertTrue(hasher.verify(secret, stored))
        self.assertFalse(hasher.verify(secret + "bad", stored))

    def test_bearer_parser(self):
        self.assertEqual(parse_api_key_header("Bearer abc"), "abc")
        self.assertEqual(parse_api_key_header("abc"), "abc")
        self.assertIsNone(parse_api_key_header("   "))

    def test_prod_rejects_demo(self):
        with self.assertRaises(ValueError):
            validate_production_credentials(
                environment="prod", master_key="x" * 40,
                service_keys={}, allow_demo_credentials=True
            )

    def test_prod_requires_services(self):
        with self.assertRaises(ValueError):
            validate_production_credentials(
                environment="prod", master_key="x" * 40,
                service_keys={"MEMORA": "short"}, allow_demo_credentials=False
            )


if __name__ == "__main__":
    unittest.main()
