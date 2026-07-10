from typing import Optional


class Card:
    token: str = "tok"


def charge(card: Optional[Card]) -> str:
    if card is None:
        return ""
    return card.token
