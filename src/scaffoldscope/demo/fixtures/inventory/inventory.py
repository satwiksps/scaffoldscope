"""Tiny deliberately broken fixture for the ScaffoldScope offline demo."""


def cart_total(prices):
    """Return the total price for all line items."""
    return -sum(prices)
