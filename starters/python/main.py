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

import math
from risk import calculate_risk
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import asyncio
import fcntl

app = FastAPI()
# Shared lock across Uvicorn processes.
RISK_LOCK_FILE = "/tmp/obsidio-risk.lock"

PRICES = {
    "AAPL": 187.42, "GOOG": 141.80, "MSFT": 412.30, "AMZN": 178.10,
    "NVDA": 120.15, "META": 502.60, "TSLA": 244.70, "JPM": 198.35,
}

SERIES = {
    s: [base * (1 + math.sin(i) / 50) for i in range(500)]
    for s, base in PRICES.items()
}


@app.get("/health")
def health():
    return {"status": "ok"}


# CHEAP (weight 1)
@app.get("/price")
def price(symbol: str):
    if symbol not in PRICES:
        raise HTTPException(status_code=404, detail="unknown symbol")

    return {
        "symbol": symbol,
        "price": PRICES[symbol]
    }


# MEDIUM (weight 3)
@app.get("/stats")
def stats(symbol: str):
    series = SERIES.get(symbol)

    if series is None:
        raise HTTPException(status_code=404, detail="unknown symbol")

    n = len(series)
    mean = sum(series) / n
    variance = sum((x - mean) ** 2 for x in series) / n

    return {
        "symbol": symbol,
        "mean": mean,
        "min": min(series),
        "max": max(series),
        "stddev": math.sqrt(variance)
    }


# OPTIONAL persistence endpoint
class PriceUpdate(BaseModel):
    symbol: str
    price: float


@app.post("/price")
def update_price(update: PriceUpdate):
    PRICES[update.symbol] = update.price

    return {
        "symbol": update.symbol,
        "price": update.price
    }


# HEAVY (weight 10): 50000 SHA-256 iterations.
@app.get("/risk")
async def risk(seed: str = "none"):
    # Only allow one direct risk calculation at a time across both workers.
    with open(RISK_LOCK_FILE, "w") as lock_file:
        await asyncio.to_thread(
            fcntl.flock,
            lock_file.fileno(),
            fcntl.LOCK_EX
        )

        try:
            h = calculate_risk(seed)
        finally:
            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_UN
            )

    return {"seed": seed, "risk_hash": h}