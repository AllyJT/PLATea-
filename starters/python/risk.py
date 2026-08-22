import hashlib

# HEAVY (weight 10)
def calculate_risk(seed: str):
    h = seed
    for _ in range(50000):
        h = hashlib.sha256(h.encode()).hexdigest()
    return h

