"""Fixed replies for blocked inputs."""

TOO_LONG = (
    "That message is too long for me to process. "
    "Please ask your Census question in under 2,000 characters."
)
OFF_TOPIC = (
    "I can only help with US Census data — for example population, income, housing, "
    "education or employment for states and counties — and with explaining Census terms. "
    "What would you like to know?"
)
MANIPULATION = (
    "I can't change how I work or share my instructions, but I'm happy to answer "
    "questions about US Census data."
)
_INAPPROPRIATE = "I can't help with that framing."


def inappropriate_reply(neutral_offer: str | None) -> str:
    if neutral_offer and neutral_offer.strip():
        return (
            f"{_INAPPROPRIATE} I can share neutral facts from the Census data instead, "
            f"for example: *{neutral_offer.strip()}*."
        )
    return _INAPPROPRIATE


def reply_for(label: str, neutral_offer: str | None = None) -> str:
    if label == "off_topic":
        return OFF_TOPIC
    if label == "manipulation":
        return MANIPULATION
    if label == "inappropriate":
        return inappropriate_reply(neutral_offer)
    raise ValueError(f"No fixed reply for label: {label!r}")
