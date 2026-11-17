"""Parallel retrieval with one inherited deadline, without waiting on expired work."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, wait
from contextvars import ContextVar, copy_context
from threading import BoundedSemaphore
from typing import Callable, TypeVar

T = TypeVar("T")
_DEADLINE: ContextVar[float | None] = ContextVar("retrieval_deadline", default=None)
# Bound outstanding work even if an upstream call ignores its socket timeout.
_SLOTS = BoundedSemaphore(64)


def network_timeout(default: float) -> float:
    deadline = _DEADLINE.get()
    if deadline is None:
        return default
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("检索预算已用尽，停止后续请求")
    return min(default, remaining)


def parallel_calls(calls: list[Callable[[], T]], timeout: float,
                   max_workers: int = 8) -> list[T | Exception]:
    """Return results/errors in input order; nested tasks share the earliest deadline.

    Running Python threads cannot be killed. Socket calls inherit the remaining
    budget, queued calls are cancelled, and shutdown never waits for running work.
    """
    if not calls:
        return []
    parent = _DEADLINE.get()
    deadline = time.monotonic() + max(0, timeout)
    if parent is not None:
        deadline = min(parent, deadline)
    pool = ThreadPoolExecutor(max_workers=min(max_workers, len(calls)))
    futures = {}
    results: list[T | Exception] = [TimeoutError("检索超时，已保留其他完成的结果") for _ in calls]
    token = _DEADLINE.set(deadline)
    try:
        for index, call in enumerate(calls):
            if time.monotonic() >= deadline:
                break
            if not _SLOTS.acquire(blocking=False):
                results[index] = RuntimeError("检索任务繁忙，请稍后重试")
                continue
            context = copy_context()

            def invoke(fn=call, ctx=context):
                def run():
                    network_timeout(timeout)
                    return fn()
                return ctx.run(run)

            try:
                future = pool.submit(invoke)
            except Exception:
                _SLOTS.release()
                raise
            future.add_done_callback(lambda _future: _SLOTS.release())
            futures[future] = index
        if futures:
            done, _ = wait(futures, timeout=max(0, deadline - time.monotonic()))
            for future in done:
                try:
                    results[futures[future]] = future.result()
                except Exception as error:
                    results[futures[future]] = error
        return results
    finally:
        _DEADLINE.reset(token)
        for future in futures:
            future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)


def timed_call(call: Callable[[], T], timeout: float) -> T:
    result = parallel_calls([call], timeout, max_workers=1)[0]
    if isinstance(result, Exception):
        raise result
    return result
