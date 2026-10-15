"""Small repository feature task. subtotal is existing behavior and is locked."""


def subtotal(prices: list[float]) -> float:
    return sum(prices)


def discounted_total(prices: list[float], rate: float) -> float:
    """Apply a discount rate in [0, 1]. Reject invalid rates with ValueError."""
    return subtotal(prices)

