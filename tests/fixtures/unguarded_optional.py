from typing import Optional


class Card:
    token: str = "tok"


def charge(card: Optional[Card]) -> str:
    return card.token
