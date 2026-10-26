"""Fixture: an application that blocks its own event loop."""
import time


async def main(phone):
    time.sleep(60)
