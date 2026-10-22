from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any, TypeVar

T = TypeVar("T")


class CancellationToken:
    """Cooperative cancellation signal that can interrupt an awaited operation."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        """Request cancellation. Repeated calls are safe."""
        self._event.set()

    def is_cancelled(self) -> bool:
        """Return whether cancellation has been requested."""
        return self._event.is_set()

    async def wait(self) -> None:
        """Wait until cancellation is requested."""
        await self._event.wait()

    def throw_if_cancelled(self) -> None:
        """Raise the standard asyncio cancellation signal when already cancelled."""
        if self.is_cancelled():
            raise asyncio.CancelledError()

    async def run(self, awaitable: Awaitable[T]) -> T:
        """Run an awaitable until it completes or this token is cancelled.

        The operation is cancelled and awaited before cancellation propagates, so
        its cleanup blocks can release resources. Cancelling the caller also
        cancels the operation and the token waiter.
        """
        return await await_or_cancel(awaitable, self)


async def await_or_cancel(awaitable: Awaitable[T], token: CancellationToken) -> T:
    """Race an operation against a cancellation token without leaking tasks."""
    operation = asyncio.ensure_future(awaitable)
    cancellation = asyncio.create_task(token.wait(), name="futuris-cancellation-wait")
    try:
        done, _ = await asyncio.wait(
            (operation, cancellation), return_when=asyncio.FIRST_COMPLETED
        )
        if cancellation in done and token.is_cancelled():
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            raise asyncio.CancelledError()
        return await operation
    except BaseException:
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        raise
    finally:
        cancellation.cancel()
        await asyncio.gather(cancellation, return_exceptions=True)


async def gather_cancel_safe(*awaitables: Awaitable[Any]) -> list[Any]:
    """Gather awaitables and cancel/drain siblings if any operation fails.

    Unlike a bare asyncio.gather, this helper guarantees that sibling tasks are
    asked to stop and their cancellation cleanup is awaited before an error or
    caller cancellation is propagated. Already-completed tasks are left alone.
    """
    tasks: list[asyncio.Future[Any]] = []
    try:
        for awaitable in awaitables:
            tasks.append(asyncio.ensure_future(awaitable))
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        raise
