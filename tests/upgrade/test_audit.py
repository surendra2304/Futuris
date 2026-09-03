import unittest
from futuris.upgrade.audit import AuditLog


class AuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_redaction_and_tenant_scope(self):
        audit = AuditLog()
        await audit.record("tenant-a", "p1", "forecast.create", "f1", "ok", token="secret")
        await audit.record("tenant-b", "p2", "forecast.create", "f2", "ok", api_key="key")
        rows = await audit.query(tenant_id="tenant-a")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].metadata["token"], "[REDACTED]")


if __name__ == "__main__":
    unittest.main()
