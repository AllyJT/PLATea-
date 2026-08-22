import asyncio


class LifoSemaphore(asyncio.Semaphore):
    """asyncio.Semaphore wakes the OLDEST waiter first (_wake_up_next scans
    self._waiters oldest-to-newest). Under sustained overload that means
    every fresh arrival queues up behind an ever-growing backlog and the
    whole burst tends to time out together. This overrides just that scan
    to go newest-to-oldest instead: a freshly arrived request can jump an
    already-stale backlog and get served while it's still useful, at the
    cost of starving old waiters that were likely doomed anyway. Everything
    else (acquire/release, the cancellation/race bookkeeping) is reused
    unchanged from the base class -- only wakeup order changes."""

    def _wake_up_next(self):
        if not self._waiters:
            return False
        for fut in reversed(self._waiters):
            if not fut.done():
                self._value -= 1
                fut.set_result(True)
                return True
        return False