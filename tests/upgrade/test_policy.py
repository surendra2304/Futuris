import unittest
from unittest.mock import patch
from futuris.upgrade.policy import Policy, PolicyEngine


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.engine = PolicyEngine(Policy(allowed_domains=frozenset({"example.com"})))

    def test_horizon_policy(self):
        self.assertTrue(self.engine.evaluate_forecast(24, 14).allowed)
        self.assertFalse(self.engine.evaluate_forecast(24 * 100, 14).allowed)

    def test_connector_policy(self):
        self.assertTrue(self.engine.evaluate_connector("llm").allowed)
        self.assertFalse(self.engine.evaluate_connector("shell").allowed)

    @patch(
        "futuris.upgrade.policy.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
    )
    def test_domain_allowlist(self, _mock_dns):
        self.assertTrue(self.engine.evaluate_url("https://example.com").allowed)
        self.assertFalse(self.engine.evaluate_url("https://example.org").allowed)

    @patch(
        "futuris.upgrade.policy.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("127.0.0.1", 0))],
    )
    def test_private_destination_blocked(self, _mock_dns):
        self.assertFalse(self.engine.evaluate_url("https://example.com").allowed)


if __name__ == "__main__":
    unittest.main()
