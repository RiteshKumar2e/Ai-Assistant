"""
Python 3.10 compatibility shim for asyncio.TaskGroup / BaseExceptionGroup.

Both were introduced in Python 3.11 (PEP 654's exception groups, and the
TaskGroup structured-concurrency API built on top of them). main.py runs every
long-lived coroutine of a session — audio in/out, the Gemini receive loop,
background monitors, the reconnect watcher — inside ONE TaskGroup, so that if
any single one of them dies, the whole session tears down together and the
outer reconnect loop rebuilds it from scratch. That is exactly the "structured
concurrency" contract TaskGroup exists to provide.

On 3.11+ this module is a transparent pass-through to the real, native
implementations — nothing below is used. Below 3.11, TaskGroup and
BaseExceptionGroup don't exist at all, so this provides minimal,
behavior-compatible replacements: same create_task()/async-context-manager
shape, same "the first child failure cancels every sibling and raises a group"
contract, same `.exceptions` attribute that main.py's `_is_reconnect_signal`
and `_keep_context_of` unwrap.
"""
from __future__ import annotations

import asyncio
import sys

if sys.version_info >= (3, 11):
    import builtins
    TaskGroup = asyncio.TaskGroup
    BaseExceptionGroup = builtins.BaseExceptionGroup
    ExceptionGroup = builtins.ExceptionGroup

else:
    class BaseExceptionGroup(BaseException):
        """Minimal stand-in for the 3.11+ builtin of the same name — carries the
        list of exceptions raised by a TaskGroup's failed child tasks."""

        def __init__(self, message: str, exceptions):
            super().__init__(message)
            self.message = message
            self.exceptions = list(exceptions)

        def __str__(self) -> str:
            return f"{self.message} ({len(self.exceptions)} sub-exception(s))"

        def __repr__(self) -> str:
            return f"BaseExceptionGroup({self.message!r}, {self.exceptions!r})"

    class ExceptionGroup(BaseExceptionGroup, Exception):
        """Same as above, restricted to Exception (not BaseException) members —
        unused directly by this app today, kept for parity with the real pair."""
        pass

    class TaskGroup:
        """Polyfill of asyncio.TaskGroup's public contract:

            async with TaskGroup() as tg:
                tg.create_task(coro())
                ...

        On a clean exit, waits for every child task. The moment any child task
        raises, every other still-running child is cancelled; once they have
        all settled, a BaseExceptionGroup wrapping every raised exception is
        thrown from __aexit__ — matching what code written against the real
        TaskGroup expects.
        """

        def __init__(self) -> None:
            self._tasks: list[asyncio.Task] = []
            self._entered = False

        async def __aenter__(self) -> "TaskGroup":
            self._entered = True
            return self

        def create_task(self, coro, *, name: str | None = None) -> asyncio.Task:
            if not self._entered:
                raise RuntimeError("TaskGroup.create_task() called before __aenter__")
            task = asyncio.ensure_future(coro)
            if name:
                task.set_name(name)
            self._tasks.append(task)
            return task

        @staticmethod
        async def _cancel_and_drain(tasks) -> None:
            for t in tasks:
                if not t.done():
                    t.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            # The `async with` BODY itself raised (not a child task) — real
            # TaskGroup cancels every child and lets that exception propagate
            # unchanged. Do the same.
            if exc is not None:
                await self._cancel_and_drain(self._tasks)
                return False

            errors: list[BaseException] = []
            pending = set(self._tasks)
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                newly_failed = [t for t in done if not t.cancelled() and t.exception() is not None]
                errors.extend(t.exception() for t in newly_failed)
                if newly_failed and pending:
                    await self._cancel_and_drain(pending)
                    pending = set()

            if errors:
                raise BaseExceptionGroup("unhandled errors in a TaskGroup", errors)
            return False
