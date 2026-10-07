"""Persistent conversation state used by the PocketFlow shared store."""

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from picli.memory.compaction import CompactionPlan, estimate_tokens
from picli.skills.registry import SkillRegistry


MESSAGE_ROLES = {"user", "assistant", "tool"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MessageBranch(list[dict]):
    """List-compatible active branch that records appended messages as tree nodes."""

    def __init__(self, session: "Session", messages: list[dict]):
        super().__init__(messages)
        self.session = session

    def append(self, message: dict) -> None:
        self.session._append_node(message)
        super().append(message)

    def extend(self, messages) -> None:
        for message in messages:
            self.append(message)


class Session:
    def __init__(
        self,
        workspace: Path,
        session: str | None,
        skills: SkillRegistry,
    ):
        self.workspace = workspace
        self.directory = workspace / ".pi-python" / "sessions"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.name = ""
        self.nodes: list[dict] = []
        self.head_id: str | None = None
        self.compactions: list[dict] = []
        self.messages = MessageBranch(self, [])
        self.skills = skills
        self.select(session)

    @staticmethod
    def _valid_name(name: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", name):
            raise ValueError("Session name: 1–80 letters, digits, underscores or hyphens")
        return name

    @staticmethod
    def _valid_message(message: object) -> dict:
        if not isinstance(message, dict) or message.get("role") not in MESSAGE_ROLES:
            raise ValueError("Invalid session message")
        return message

    def select(self, name: str | None = None) -> None:
        name = self._valid_name(name or uuid.uuid4().hex)
        path = self.directory / f"{name}.json"
        if not path.exists():
            self.name, self.nodes, self.head_id, self.compactions = name, [], None, []
            self.messages = MessageBranch(self, [])
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("version") == 3:
            self._load_tree(name, data)
            return
        if isinstance(data, list):
            messages, summary = data, ""
        elif isinstance(data, dict):
            messages, summary = data.get("messages"), data.get("summary", "")
        else:
            raise ValueError("Invalid session file")
        if (
            not isinstance(messages, list)
            or not all(
                isinstance(item, dict) and item.get("role") in MESSAGE_ROLES
                for item in messages
            )
            or not isinstance(summary, str)
        ):
            raise ValueError("Invalid session file")
        self.name, self.nodes, self.head_id, self.compactions = name, [], None, []
        for message in messages:
            self._append_node(message)
        if summary:
            self.compactions.append(
                {
                    "id": uuid.uuid4().hex,
                    "through_id": None,
                    "summary": summary,
                    "created_at": now(),
                    "legacy_prefix": True,
                }
            )
        self._refresh_messages()

    def save(self) -> None:
        path = self.directory / f"{self.name}.json"
        temporary = path.with_suffix(".tmp")
        data = {
            "version": 3,
            "head_id": self.head_id,
            "nodes": self.nodes,
            "compactions": self.compactions,
        }
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _load_tree(self, name: str, data: dict) -> None:
        nodes = data.get("nodes")
        head_id = data.get("head_id")
        compactions = data.get("compactions", [])
        if not isinstance(nodes, list) or not isinstance(compactions, list):
            raise ValueError("Invalid session tree")
        node_ids: set[str] = set()
        for node in nodes:
            if (
                not isinstance(node, dict)
                or not isinstance(node.get("id"), str)
                or node["id"] in node_ids
                or (node.get("parent_id") is not None and not isinstance(node.get("parent_id"), str))
                or not isinstance(node.get("created_at"), str)
            ):
                raise ValueError("Invalid session tree node")
            self._valid_message(node.get("message"))
            node_ids.add(node["id"])
        if head_id is not None and head_id not in node_ids:
            raise ValueError("Invalid session tree head")
        by_id = {node["id"]: node for node in nodes}
        for node in nodes:
            seen: set[str] = set()
            current: dict | None = node
            while current is not None:
                identifier = current["id"]
                if identifier in seen:
                    raise ValueError("Session tree contains a cycle")
                seen.add(identifier)
                parent_id = current.get("parent_id")
                if parent_id is None:
                    current = None
                elif parent_id not in by_id:
                    raise ValueError("Session tree references a missing parent")
                else:
                    current = by_id[parent_id]
        for compaction in compactions:
            if (
                not isinstance(compaction, dict)
                or not isinstance(compaction.get("id"), str)
                or not isinstance(compaction.get("summary"), str)
                or not isinstance(compaction.get("created_at"), str)
                or (
                    compaction.get("through_id") is not None
                    and compaction.get("through_id") not in node_ids
                )
            ):
                raise ValueError("Invalid session compaction")
        self.name = name
        self.nodes = nodes
        self.head_id = head_id
        self.compactions = compactions
        self._refresh_messages()

    def _append_node(self, message: dict) -> str:
        self._valid_message(message)
        identifier = uuid.uuid4().hex
        self.nodes.append(
            {
                "id": identifier,
                "parent_id": self.head_id,
                "message": message,
                "created_at": now(),
            }
        )
        self.head_id = identifier
        return identifier

    def _node_map(self) -> dict[str, dict]:
        return {node["id"]: node for node in self.nodes}

    def branch_nodes(self, head_id: str | None = None) -> list[dict]:
        head_id = self.head_id if head_id is None else head_id
        if head_id is None:
            return []
        by_id = self._node_map()
        if head_id not in by_id:
            raise ValueError(f"Unknown session node: {head_id}")
        branch: list[dict] = []
        current: dict | None = by_id[head_id]
        while current is not None:
            branch.append(current)
            parent_id = current.get("parent_id")
            current = by_id.get(parent_id) if parent_id else None
        branch.reverse()
        return branch

    @property
    def all_messages(self) -> list[dict]:
        return [node["message"] for node in self.branch_nodes()]

    def _active_compaction(self) -> dict | None:
        ancestors = {node["id"] for node in self.branch_nodes()}
        applicable = [
            item
            for item in self.compactions
            if item.get("through_id") is None or item.get("through_id") in ancestors
        ]
        return applicable[-1] if applicable else None

    def _context_nodes(self) -> list[dict]:
        branch = self.branch_nodes()
        compaction = self._active_compaction()
        if compaction is None or compaction.get("through_id") is None:
            return branch
        for index, node in enumerate(branch):
            if node["id"] == compaction["through_id"]:
                return branch[index + 1 :]
        return branch

    def _refresh_messages(self) -> None:
        self.messages = MessageBranch(self, [node["message"] for node in self._context_nodes()])

    @property
    def summary(self) -> str:
        compaction = self._active_compaction()
        return compaction["summary"] if compaction else ""

    def resolve_node(self, reference: str) -> str:
        exact = [node["id"] for node in self.nodes if node["id"] == reference]
        if exact:
            return exact[0]
        matches = [node["id"] for node in self.nodes if node["id"].startswith(reference)]
        if not matches:
            raise ValueError(f"Unknown session node: {reference}")
        if len(matches) > 1:
            raise ValueError(f"Ambiguous session node prefix: {reference}")
        return matches[0]

    def checkout(self, reference: str) -> str:
        self.head_id = self.resolve_node(reference)
        self._refresh_messages()
        self.save()
        return self.head_id

    def fork(self, name: str, reference: str | None = None) -> None:
        name = self._valid_name(name)
        if name in self.list_names():
            raise ValueError(f"Session already exists: {name}")
        head_id = self.resolve_node(reference) if reference else self.head_id
        branch = self.branch_nodes(head_id)
        branch_ids = {node["id"] for node in branch}
        compactions = [
            item
            for item in self.compactions
            if item.get("through_id") is None or item.get("through_id") in branch_ids
        ]
        data = {
            "version": 3,
            "head_id": head_id,
            "nodes": branch,
            "compactions": compactions,
        }
        self._create_fork(name, data)
        self.select(name)

    def list_names(self) -> list[str]:
        return sorted(path.stem for path in self.directory.glob('*.json'))

    def _create_fork(self, name: str, data: dict) -> None:
        path = self.directory / f'{name}.json'
        with path.open('x', encoding='utf-8') as output:
            json.dump(data, output, ensure_ascii=False, indent=2)

    def format_tree(self) -> str:
        if not self.nodes:
            return "当前会话还没有消息节点。"
        children: dict[str | None, list[dict]] = {}
        for node in self.nodes:
            children.setdefault(node.get("parent_id"), []).append(node)
        active = {node["id"] for node in self.branch_nodes()}
        lines: list[str] = []

        def visit(node: dict, prefix: str, last: bool) -> None:
            identifier = node["id"]
            marker = ">" if identifier == self.head_id else "*" if identifier in active else " "
            connector = "└─" if last else "├─"
            message = node["message"]
            content = message.get("content")
            if not content and message.get("tool_calls"):
                names = [call.get("function", {}).get("name", "?") for call in message["tool_calls"]]
                content = "tool calls: " + ", ".join(names)
            preview = " ".join(str(content or "").split())[:72] or "(empty)"
            lines.append(f"{prefix}{connector}{marker} {identifier[:8]} {message['role']}: {preview}")
            descendants = children.get(identifier, [])
            child_prefix = prefix + ("  " if last else "│ ")
            for index, child in enumerate(descendants):
                visit(child, child_prefix, index == len(descendants) - 1)

        roots = children.get(None, [])
        for index, root in enumerate(roots):
            visit(root, "", index == len(roots) - 1)
        return "\n".join(lines)

    def system_message(self, allow_bash: bool, permission_mode: str = "workspace-write") -> dict:
        text = (
            "You are a terminal coding assistant. Complete user tasks using read, write, edit, bash. "
            "Read before editing and report tool failures honestly. Relative paths use the workspace. Absolute and "
            "~/ paths can address the host, subject to the active permission mode. A safety policy automatically "
            "allows ordinary workspace file operations, asks before sensitive or host-wide operations in "
            "workspace-write mode, and permanently denies destructive system actions and credential access. Never "
            "try to bypass this policy or claim that a denied operation succeeded. "
            "Use MEMORY.md files for durable working memory. Update the global file for project facts and preferences, "
            "and the session file for decisions and ongoing work specific to this conversation. Keep memory concise. "
            "When the user corrects a fact, replace the obsolete value with only the current canonical value; do not "
            "retain or mention superseded values unless the user explicitly asks to preserve their history. Revise "
            "existing facts instead of appending duplicates. The newest explicit user correction takes precedence "
            "over older conversation messages, summaries, and memory entries. "
            f"Workspace: {self.workspace}. Permission mode: {permission_mode}. "
            f"Bash available: {allow_bash}. In read-only mode, do not request write, edit, or bash. "
            "In workspace-write mode, ordinary workspace file changes are allowed while higher-risk operations "
            "require user approval. Danger-full-access automatically approves ASK operations, but hard DENY "
            "safety rules still apply."
        )
        for name in ("AGENTS.md",):
            path = self.workspace / name
            if path.is_file():
                with path.open(encoding="utf-8") as source:
                    text += f"\n\n{name}:\n{source.read()}"
        global_memory = self.workspace / "MEMORY.md"
        session_memory = self.session_memory_path
        if global_memory.is_file():
            text += "\n\n### Global Workspace Memory\n" + global_memory.read_text(encoding="utf-8").strip()
        if session_memory.is_file():
            text += "\n\n### Session-Specific Memory\n" + session_memory.read_text(encoding="utf-8").strip()
        text += (
            f"\n\nMemory locations:\n- Global: {global_memory.relative_to(self.workspace)}"
            f"\n- Session: {session_memory.relative_to(self.workspace)}"
        )
        catalog = self.skills.catalog()
        if catalog:
            text += "\n\nProject Skills:\n" + catalog
        return {"role": "system", "content": text}

    def context(self, allow_bash: bool, permission_mode: str = "workspace-write") -> list[dict]:
        system = self.system_message(allow_bash, permission_mode)
        if self.summary:
            system["content"] += "\n\nEarlier conversation summary:\n" + self.summary
        return [system, *self.messages]

    @property
    def session_memory_path(self) -> Path:
        return self.workspace / ".pi-memory" / self.name / "MEMORY.md"

    def context_tokens(self, allow_bash: bool, permission_mode: str = "workspace-write") -> int:
        return estimate_tokens(self.context(allow_bash, permission_mode))

    def apply_compaction(self, plan: CompactionPlan, summary: str) -> None:
        context_nodes = self._context_nodes()
        if not plan.compact or len(plan.compact) > len(context_nodes):
            raise ValueError("Compaction plan does not match the active session branch")
        compact_nodes = context_nodes[: len(plan.compact)]
        if [node["message"] for node in compact_nodes] != plan.compact:
            raise ValueError("Compaction plan does not match the active session branch")
        archive = self.workspace / ".pi-python" / "archives"
        archive.mkdir(parents=True, exist_ok=True)
        record = {
            "compacted_at": now(),
            "session": self.name,
            "previous_summary": self.summary,
            "node_ids": [node["id"] for node in compact_nodes],
            "messages": plan.compact,
        }
        with (archive / f"{self.name}.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.compactions.append(
            {
                "id": uuid.uuid4().hex,
                "through_id": compact_nodes[-1]["id"],
                "summary": summary,
                "created_at": now(),
            }
        )
        self._refresh_messages()
        self.save()
