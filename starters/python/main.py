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
import time 

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

RISK_POOL = ProcessPoolExecutor(max_workers=1)
## Semaphore limits the number of concurrent risk calculations;
## env-tunable
## so sweep-risk-params.sh can search RISK_SLOTS / RISK_QUEUE_TIMEOUT.
RISK_SLOTS = asyncio.Semaphore(int(os.environ.get("RISK_SLOTS", 2)))
RISK_QUEUE_TIMEOUT = float(os.environ.get("RISK_QUEUE_TIMEOUT", 1))

# ADD: A second time out for those so we can cut the one waiting too long
RISK_WAIT_TIMEOUT = float(os.environ.get("RISK_WAIT_TIMEOUT", 2))


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
        ## Try to acquire a slot for risk calculation,
        ## add a time out so we can return a 503 if the risk service is overloaded
        await asyncio.wait_for(
            RISK_SLOTS.acquire(),
            timeout=RISK_QUEUE_TIMEOUT
        )
        ## if it is overloaded, return a 503 error
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=503,
            detail="risk service overloaded"
        )
# Running the risk calculation when we have acquired a slot
# Calculate the risk in a separate process so it does not block the main thread
    try:
        # keep track of which event is ready to run next
        event_loop = asyncio.get_running_loop()
        # calculate the risk , its being done by the process pool executor (another thread), 
        # so it does not block the main thread
        # the risk is being calculated in the background
        result_of_risk = event_loop.run_in_executor(
            RISK_POOL, calculate_risk, seed
        )
        # wait for the result of the risk calculation to be ready
        # let the low and medium endpoints run first while the risk is being calculated

        try:
            h = await asyncio.wait_for(result_of_risk, timeout=RISK_WAIT_TIMEOUT)
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=503,
                detail="risk service overloaded"
            )

        return {"seed": seed, "risk_hash": h}

    finally:
        RISK_SLOTS.release()