from dataclasses import dataclass

@dataclass(frozen=True)
class Plugin:
    name: str
    aliases: tuple[str, ...] = ()
