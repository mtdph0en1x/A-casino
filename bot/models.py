from enum import Enum


class BetStatus(str, Enum):
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    CLAIMED = "CLAIMED"
    CONFIRMED = "CONFIRMED"
    DISPUTED = "DISPUTED"
    DECLINED = "DECLINED"


# Starting balance every new user gets
STARTING_BALANCE = 1.0

# Votes from non-participants needed to resolve a dispute
MIN_VOTES_TO_RESOLVE = 2


def fmt(amount: float) -> str:
    """Format a жопа amount for display."""
    return f"{amount:g} 🍑"


def display_name(user_row) -> str:
    """Best display name for a user row."""
    if user_row.get("username"):
        return f"@{user_row['username']}"
    return user_row.get("first_name", "")
