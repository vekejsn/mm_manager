"""Fixture: an application that fails after loading."""


async def main(phone):
    raise RuntimeError('Intentional test application failure')
