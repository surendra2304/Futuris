import unittest
from futuris.upgrade.models import ActionRisk
from futuris.upgrade.tool_registry import ToolDenied, ToolRegistry, ToolSpec


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.register(ToolSpec("observe", lambda: 1, ActionRisk.OBSERVE, frozenset({"read"})))
        self.registry.register(ToolSpec("governed", lambda: 2, ActionRisk.GOVERNED, frozenset({"write"})))

    def test_scope(self):
        spec = self.registry.allowed("observe", scopes=frozenset({"read"}), approved=False)
        self.assertEqual(spec.name, "observe")

    def test_governed_requires_approval(self):
        with self.assertRaises(ToolDenied):
            self.registry.allowed("governed", scopes=frozenset({"write"}), approved=False)


if __name__ == "__main__":
    unittest.main()
