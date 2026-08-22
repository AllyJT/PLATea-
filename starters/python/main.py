# Obsidio starter: Python + FastAPI. NAIVE ON PURPOSE.
#
# Implements the contract correctly, so it builds, runs, and passes the health
# check. But it runs a SINGLE uvicorn worker, and the /risk computation is
# CPU-bound, so it blocks and clogs /price behind it under load. It WILL fail
# the load test. Making it resilient is YOUR job.
#
# There is deliberately NO resilience machinery here: no extra workers, no
# process pool, no caching, no queueing. That is the part you build.
#
# Core-count note: your container is capped at 2 CPUs but can SEE all host
# cores. If you add uvicorn/gunicorn workers, set the count explicitly (e.g. 2);
# do not let the server auto-size from the visible core count.

import asyncio
import math
import os
from risk import calculate_risk
from concurrent.futures import ProcessPoolExecutor
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from multiprocessing import Pool
import time

from lifoSemaphore import LifoSemaphore

app = FastAPI()

PRICES = {
    "AAPL": 187.42, "GOOG": 141.80, "MSFT": 412.30, "AMZN": 178.10,
    "NVDA": 120.15, "META": 502.60, "TSLA": 244.70, "JPM": 198.35,
}
SERIES = {s: [base * (1 + math.sin(i) / 50) for i in range(500)]
          for s, base in PRICES.items()}


@app.get("/health")
def health():
    return {"status": "ok"}


# CHEAP (weight 1)
@app.get("/price")
def price(symbol: str):
    if symbol not in PRICES:
        raise HTTPException(status_code=404, detail="unknown symbol")
    return {"symbol": symbol, "price": PRICES[symbol]}


# MEDIUM (weight 3)
@app.get("/stats")
def stats(symbol: str):
    series = SERIES.get(symbol)
    if series is None:
        raise HTTPException(status_code=404, detail="unknown symbol")
    n = len(series)
    mean = sum(series) / n
    variance = sum((x - mean) ** 2 for x in series) / n
    return {"symbol": symbol, "mean": mean,
            "min": min(series), "max": max(series),
            "stddev": math.sqrt(variance)}


# OPTIONAL, only for the persistence bonus. In-memory only, so it does NOT
# survive a restart. Add real persistence (and pay its cost) to claim the bonus.
class PriceUpdate(BaseModel):
    symbol: str
    price: float


@app.post("/price")
def update_price(update: PriceUpdate):
    PRICES[update.symbol] = update.price
    return {"symbol": update.symbol, "price": update.price}

# Prioritize the /price and /stats endpoints over /risk,
# so that a heavy /risk request does not block the others.


def _deprioritize_risk_worker():
    """Runs once at startup in each risk-pool worker process. Lowers this
    process's OS scheduling priority (nice) so the kernel favors /price and
    /stats -- which run at normal priority -- whenever both want the CPU
    at the same moment. Unlike CPU pinning, a niced-down process still
    gets to use full CPU (even both cores) whenever nothing higher-
    priority needs it; it only yields under actual contention. This is
    what lets RISK_SLOTS be raised for more real risk-tier capacity
    without that costing /price / /stats latency the way it did before."""
    os.nice(15)


RISK_POOL = ProcessPoolExecutor(max_workers=5, initializer=_deprioritize_risk_worker)
## Semaphore limits the number of concurrent risk calculations;
## env-tunable
## so sweep-risk-params.sh can search RISK_SLOTS / RISK_QUEUE_TIMEOUT.
# Change from First in First out to Last in First out, reduce the time wait-z
RISK_SLOTS = LifoSemaphore(int(os.environ.get("RISK_SLOTS", 2)))

# Total time willing to keep retrying for a slot before actually giving up
# (503). Spends risk's unused p95 margin on retries instead of rejecting
# on the first miss. Because RISK_SLOTS is a LifoSemaphore, each retry
# re-enters the queue as the NEWEST waiter -- it jumps back to the front
# instead of just sitting in place, so retrying is not the same as a
# longer single wait.
RISK_ADMIT_BUDGET = float(os.environ.get("RISK_ADMIT_BUDGET", 1.2))


async def _acquire_with_retry(sem, total_budget):
    loop = asyncio.get_running_loop()
    start = loop.time()
    while True:
        remaining = total_budget - (loop.time() - start)
        if remaining <= 0:
            raise asyncio.TimeoutError
        try:
            await asyncio.wait_for(sem.acquire(), timeout=remaining)
            return
        except asyncio.TimeoutError:
            continue  # re-enter as the newest LIFO waiter, try again



# HEAVY (weight 10): 50000 iterations of SHA-256 over the seed. Uncacheable.
@app.get("/risk")
# Make the risk endpoint async so it can be pause, allow other low 
# and medium weight to run first while risk is being calculated by
# # 
# async def risk(seed: str = "none"):
#     # keep track of which event is ready to run next
#     event_loop = asyncio.get_running_loop() 

#     # calculate the risk , its being done by the process pool executor (another thread), 
#     # so it does not block the main thread
#     # the risk is being calculated in the background
#     result_of_risk = event_loop.run_in_executor(RISK_POOL, calculate_risk, seed)

#     # wait for the result of the risk calculation to be ready
#     # let the low and medium endpoints run first while the risk is being calculated
#     h = await result_of_risk
#     return {"seed": seed, "risk_hash": h}

async def risk(seed: str = "none"):
    try:
        ## Try to acquire a slot for risk calculation, retrying (as the
        ## newest LIFO waiter each time) until RISK_ADMIT_BUDGET runs out,
        ## instead of giving up on the first miss.
        await _acquire_with_retry(RISK_SLOTS, RISK_ADMIT_BUDGET)
        ## if it is overloaded, return a 503 error
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=503,
            detail="risk service overloaded"
        )
# Running the risk calculation when we have acquired a slot
# Calculate the risk in a separate process so it does not block the main thread
    event_loop = asyncio.get_running_loop()
    result_of_risk = event_loop.run_in_executor(
        RISK_POOL, calculate_risk, seed
    )
    # Release the slot only once the job itself finishes (or is cancelled
    # before it started running) -- not when the client stops waiting for
    # it. If we released on client timeout instead, a new request could be
    # admitted while the old job is still occupying the pool's one worker,
    # letting more jobs pile up than RISK_SLOTS was meant to allow.
    result_of_risk.add_done_callback(lambda _: RISK_SLOTS.release())

    h = await result_of_risk

    return {"seed": seed, "risk_hash": h}