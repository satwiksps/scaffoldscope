"""Tiny deliberately broken fixture for the ScaffoldScope offline demo."""

import re


def slugify(text):
    """Convert words separated by whitespace into a lowercase slug."""
    return text.strip().lower().replace(" ", "-")
