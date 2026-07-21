"""
Fake payment provider — simulates a Stripe-like payment API.

Design notes:
- charge() simulates a network call with a small delay
- Returns success/failure based on amount (amounts ending in .99 fail,
  for easy testing of failure paths)
- Generates a fake provider_ref (like Stripe's charge ID)
- refund() is a stub for future use

In production this would be replaced with a real Stripe/Square adapter.
The interface stays the same — the rest of the system doesn't care which
provider is behind it.
"""
import uuid
import asyncio


class FakePaymentProvider:
    """In-memory fake that simulates Stripe-like behavior."""

    async def charge(
        self,
        amount: float,
        idempotency_key: str,
    ) -> dict:
        """Simulate charging a card.

        Returns:
            {"success": True, "provider_ref": "ch_xxx"}
          or
            {"success": False, "error": "...", "provider_ref": "ch_xxx"}
        """
        # Simulate network latency
        await asyncio.sleep(0.1)

        provider_ref = f"ch_{uuid.uuid4().hex[:16]}"

        # Amounts ending in .99 simulate a declined card (for testing)
        if amount % 1 == 0.99:
            return {
                "success": False,
                "error": "Card declined by issuer",
                "provider_ref": provider_ref,
            }

        return {
            "success": True,
            "provider_ref": provider_ref,
        }

    async def refund(self, provider_ref: str) -> dict:
        """Stub for future refund support."""
        await asyncio.sleep(0.05)
        return {
            "success": True,
            "refund_id": f"rf_{uuid.uuid4().hex[:16]}",
        }
