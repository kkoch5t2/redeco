"""Compatibility entry point for the original isolated-only check."""
import asyncio
from check_runtime import verify

if __name__ == "__main__":
    asyncio.run(verify(mode="isolated", expected_user="redeco"))
