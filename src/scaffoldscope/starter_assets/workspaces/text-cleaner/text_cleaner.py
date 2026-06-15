"""Small deliberately broken target for the ScaffoldScope starter."""


def collapse_spaces(text):
    """Trim text and collapse whitespace runs to one ordinary space."""
    return text.strip().replace("  ", " ")
