"""Stand-in for an external payment provider (PayFast) with a timeout-guarded client."""
import asyncio
import random
import uuid

from shoplite.chaos import chaos

PROVIDER_URL = "https://api.payfast.example/v1/charges"
CLIENT_TIMEOUT_S = 3.0
KEY_HINT = "pk_live_****9f2c"


class PaymentGatewayTimeout(Exception):
    pass


class PaymentAuthError(Exception):
    pass


async def charge(amount_cents: int) -> str:
    if chaos.is_on("expired_api_key"):
        raise PaymentAuthError(
            f"POST {PROVIDER_URL} -> 401 Unauthorized: api key {KEY_HINT} expired "
            f"(since {chaos.since.get('expired_api_key')})"
        )
    delay = 8.0 if chaos.is_on("slow_payment") else random.uniform(0.05, 0.2)
    try:
        await asyncio.wait_for(asyncio.sleep(delay), timeout=CLIENT_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise PaymentGatewayTimeout(
            f"POST {PROVIDER_URL} timed out after {CLIENT_TIMEOUT_S}s (read timeout)"
        ) from None
    return f"ch_{uuid.uuid4().hex[:12]}"
