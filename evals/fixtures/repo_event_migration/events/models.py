from dataclasses import dataclass

@dataclass(frozen=True)
class Event:
    user_id: str
    kind: str
    metadata: dict
