from __future__ import annotations

import asyncio
import inspect

import pytest


@pytest.fixture(autouse=True)
def ensure_event_loop(request):
    if inspect.iscoroutinefunction(request.function):
        yield
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    if not loop.is_closed():
        loop.close()
    asyncio.set_event_loop(None)
