"""Context size estimation and safe conversation compaction."""

import json
from dataclasses import dataclass


def estimate_tokens(value: object) -> int:
    """Conservatively estimate tokens without a tokenizer dependency."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    ascii_count = sum(character.isascii() for character in text)
    return (ascii_count + 3) // 4 + len(text) - ascii_count


@dataclass(frozen=True)
class CompactionPlan:
    compact: list[dict]
    keep: list[dict]
    estimated_tokens: int


class Compactor:
    def __init__(self, context_window: int, compact_at: float, keep_turns: int):
        if not 0.1 <= compact_at <= 0.95:
            raise ValueError("compact_at must be between 0.1 and 0.95")
        self.context_window = context_window
        self.compact_at = compact_at
        self.keep_turns = keep_turns

    @property
    def threshold(self) -> int:
        return int(self.context_window * self.compact_at)

    def plan(self, system: dict, summary: str, messages: list[dict], force: bool = False) -> CompactionPlan | None:
        context = [system]
        if summary:
            context.append({"role": "system", "content": "Earlier conversation summary:\n" + summary})
        context.extend(messages)
        estimated = estimate_tokens(context)
        if not force and estimated < self.threshold:
            return None

        user_indexes = [index for index, message in enumerate(messages) if message.get("role") == "user"]
        if len(user_indexes) <= self.keep_turns:
            return None
        cut = user_indexes[-self.keep_turns]
        if cut <= 0:
            return None
        return CompactionPlan(messages[:cut], messages[cut:], estimated)

    @staticmethod
    def prompt(previous_summary: str, messages: list[dict]) -> list[dict]:
        history = json.dumps(messages, ensure_ascii=False, indent=2)
        prior = previous_summary or "(none)"
        return [
            {
                "role": "system",
                "content": (
                    "Compress a coding-agent conversation. Return only a dense factual summary. "
                    "Preserve user goals, constraints, decisions, preferences, file paths, code changes, "
                    "tool results, errors, and unfinished work. Never invent facts. The summary will replace "
                    "the older messages, so include everything needed to continue the work."
                ),
            },
            {
                "role": "user",
                "content": f"Previous summary:\n{prior}\n\nMessages to merge:\n{history}",
            },
        ]
