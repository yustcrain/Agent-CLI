"""Acceptance-driven long-running goals orchestrated by PocketFlow."""

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from pocketflow import Flow, Node

from picli.agent.acceptance import AcceptanceVerifier, format_criteria, format_results, validate_criteria
from picli.agent.flow import RunCancelled, create_agent_flow


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Goal:
    id: str
    objective: str
    status: str
    cycle: int
    max_cycles: int
    criteria: list[dict]
    verification: list[dict]
    approvals: list[str]
    progress: str
    next_prompt: str
    last_error: str
    created_at: str
    updated_at: str

    @classmethod
    def from_dict(cls, data: object) -> "Goal":
        if not isinstance(data, dict):
            raise ValueError("Invalid goal file")
        criteria_value = data.get("criteria", [])
        criteria = validate_criteria(criteria_value) if criteria_value else []
        verification = data.get("verification", [])
        approvals = data.get("approvals", [])
        if not isinstance(verification, list) or not all(isinstance(item, dict) for item in verification):
            raise ValueError("Invalid goal verification history")
        if not isinstance(approvals, list) or not all(isinstance(item, str) for item in approvals):
            raise ValueError("Invalid goal approvals")
        try:
            goal = cls(
                id=str(data["id"]),
                objective=str(data["objective"]),
                status=str(data["status"]),
                cycle=int(data["cycle"]),
                max_cycles=int(data["max_cycles"]),
                criteria=criteria,
                verification=verification,
                approvals=approvals,
                progress=str(data.get("progress", "")),
                next_prompt=str(data.get("next_prompt", "")),
                last_error=str(data.get("last_error", "")),
                created_at=str(data["created_at"]),
                updated_at=str(data["updated_at"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Invalid goal file") from error
        if (
            not goal.objective
            or goal.status not in {"active", "paused", "blocked", "complete", "cancelled"}
            or goal.cycle < 0
            or goal.max_cycles < 1
        ):
            raise ValueError("Invalid goal file")
        return goal


class GoalStore:
    def __init__(self, workspace: Path, session: str):
        self.directory = workspace / ".pi-python" / "goals"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / f"{session}.json"
        self.history = self.directory / f"{session}.history.jsonl"

    def load(self) -> Goal | None:
        if not self.path.exists():
            return None
        return Goal.from_dict(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self, goal: Goal) -> None:
        goal.updated_at = now()
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(goal), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def start(self, objective: str, max_cycles: int, criteria: list[dict]) -> Goal:
        objective = objective.strip()
        if not objective:
            raise ValueError("Goal objective cannot be empty")
        existing = self.load()
        if existing and existing.status in {"active", "paused", "blocked"}:
            raise ValueError("An unfinished goal exists; use /goal resume or /goal cancel first")
        if existing:
            self.archive(existing)
        timestamp = now()
        goal = Goal(
            id=uuid.uuid4().hex,
            objective=objective,
            status="active",
            cycle=0,
            max_cycles=max_cycles,
            criteria=validate_criteria(criteria),
            verification=[],
            approvals=[],
            progress="Acceptance contract created; no checks have run yet.",
            next_prompt="",
            last_error="",
            created_at=timestamp,
            updated_at=timestamp,
        )
        self.save(goal)
        return goal

    def archive(self, goal: Goal) -> None:
        with self.history.open("a", encoding="utf-8") as output:
            output.write(json.dumps(asdict(goal), ensure_ascii=False) + "\n")


class PrepareGoalCycleNode(Node):
    def __init__(self, store: GoalStore, max_turns: int, checkpoint=None):
        super().__init__()
        self.store = store
        self.max_turns = max_turns
        self.checkpoint = checkpoint

    def post(self, shared: dict, prep_res: None, exec_res: None) -> None:
        if self.checkpoint:
            self.checkpoint("executing")
        goal: Goal = shared["goal"]
        goal.cycle += 1
        goal.status = "active"
        goal.last_error = ""
        self.store.save(goal)
        results = format_results(goal.verification) if goal.verification else "No checks have run yet."
        prompt = (
            "Execute this persistent goal and satisfy its acceptance contract. Work autonomously and make "
            "concrete changes with the available tools. A textual claim of completion has no effect: an "
            "independent verifier will run every acceptance check after this turn. Do not weaken, delete, or "
            "bypass checks. Fix failed checks and verify your work.\n\n"
            f"Goal:\n{goal.objective}\n\nAcceptance contract:\n{format_criteria(goal.criteria)}\n\n"
            f"Latest acceptance verification:\n{results}"
        )
        shared.update(
            {
                "prompt": prompt,
                "model_turns": 0,
                "max_turns": self.max_turns,
                "tool_calls": [],
                "finish_reason": "",
                "force_compaction": False,
            }
        )


class VerifyGoalNode(Node):
    def __init__(
        self,
        verifier: AcceptanceVerifier,
        store: GoalStore,
        checkpoint=None,
    ):
        super().__init__()
        self.verifier = verifier
        self.store = store
        self.checkpoint = checkpoint

    def prep(self, shared: dict) -> tuple[list[dict], list[str]]:
        if self.checkpoint:
            self.checkpoint("verifying")
        goal: Goal = shared["goal"]
        return goal.criteria, goal.approvals

    def exec(self, prep_res: tuple[list[dict], list[str]]) -> list[dict]:
        criteria, approvals = prep_res
        results = []
        for criterion in criteria:
            if self.checkpoint:
                self.checkpoint("verifying")
            results.extend(result.as_dict() for result in self.verifier.verify([criterion], approvals))
        return results

    def post(
        self,
        shared: dict,
        prep_res: tuple[list[dict], list[str]],
        exec_res: list[dict],
    ) -> str:
        if self.checkpoint:
            self.checkpoint("verified")
        goal: Goal = shared["goal"]
        deterministic = exec_res
        goal.verification = deterministic
        goal.progress = format_results(deterministic)
        failed = [result for result in deterministic if result["status"] == "failed"]
        waiting = [result for result in deterministic if result["status"] in {"blocked", "pending"}]
        if failed:
            goal.next_prompt = "Resolve these failed acceptance checks:\n" + format_results(failed)
            if goal.cycle >= goal.max_cycles:
                goal.status = "paused"
                goal.last_error = f"Reached the {goal.max_cycles}-cycle run limit with failed checks"
            else:
                goal.status = "active"
        elif waiting:
            goal.status = "blocked"
            goal.next_prompt = ""
            goal.last_error = "Acceptance checks require user action:\n" + format_results(waiting)
        else:
            goal.status = "complete"
            goal.next_prompt = ""
        self.store.save(goal)
        return "continue" if goal.status == "active" else "finish"


class FinishGoalNode(Node):
    def post(self, shared: dict, prep_res: None, exec_res: None) -> None:
        goal: Goal = shared["goal"]
        labels = {
            "complete": "验收通过",
            "blocked": "等待用户验收或权限",
            "paused": "已暂停，可用 /goal resume 继续",
            "cancelled": "已取消",
        }
        print(f"\n[goal {labels.get(goal.status, goal.status)} | cycles: {goal.cycle}]")
        print(goal.progress)
        if goal.last_error:
            print(goal.last_error)


class GoalRunner:
    def __init__(
        self,
        agent: object,
        max_cycles: int,
        allow_manual: bool = True,
        store: GoalStore | None = None,
        event_sink=None,
        cancel_check=None,
    ):
        self.agent = agent
        self.max_cycles = max_cycles
        self.allow_manual = allow_manual
        self._store = store
        self.event_sink = event_sink
        self.cancel_check = cancel_check

    def checkpoint(self, phase: str) -> None:
        if self.cancel_check and self.cancel_check():
            raise RunCancelled()
        if self.event_sink:
            self.event_sink("goal.phase", {"phase": phase})

    def store(self) -> GoalStore:
        return self._store if self._store is not None else GoalStore(self.agent.store.workspace, self.agent.session)

    def current(self) -> Goal | None:
        return self.store().load()

    def start(self, objective: str) -> Goal:
        store = self.store()
        objective = objective.strip()
        if not objective:
            raise ValueError("Goal objective cannot be empty")
        existing = store.load()
        if existing and existing.status in {"active", "paused", "blocked"}:
            raise ValueError("An unfinished goal exists; use /goal resume or /goal cancel first")
        self.checkpoint("planning")
        criteria = self.agent.provider.create_acceptance_criteria(
            objective,
            self.agent.tools.allow_bash,
            self.allow_manual,
        )
        goal = store.start(objective, self.max_cycles, criteria)
        print("\n[acceptance contract]\n" + format_criteria(goal.criteria))
        return self._run(store, goal)

    def resume(self) -> Goal:
        store = self.store()
        goal = store.load()
        if goal is None:
            raise ValueError("No goal exists in this session")
        if goal.status in {"complete", "cancelled"}:
            raise ValueError(f"Goal is already {goal.status}")
        if not goal.criteria:
            goal.criteria = self.agent.provider.create_acceptance_criteria(
                goal.objective,
                self.agent.tools.allow_bash,
                self.allow_manual,
            )
        goal.status = "active"
        goal.cycle = 0
        goal.max_cycles = self.max_cycles
        goal.last_error = ""
        store.save(goal)
        return self._run(store, goal)

    def approve(self, criterion_id: str) -> Goal:
        goal = self._require_current()
        criterion = next((item for item in goal.criteria if item["id"] == criterion_id), None)
        if criterion is None:
            raise ValueError(f"Unknown acceptance criterion: {criterion_id}")
        if criterion["check"]["type"] != "manual":
            raise ValueError(f"{criterion_id} is machine-verified and cannot be manually approved")
        if goal.status in {"complete", "cancelled"}:
            raise ValueError(f"Goal is already {goal.status}")
        if criterion_id not in goal.approvals:
            goal.approvals.append(criterion_id)
        goal.status = "paused"
        goal.last_error = "Manual acceptance recorded; use /goal resume to rerun all checks"
        self.store().save(goal)
        return goal

    def pause(self) -> Goal:
        goal = self._require_current()
        if goal.status in {"complete", "cancelled"}:
            raise ValueError(f"Goal is already {goal.status}")
        goal.status = "paused"
        self.store().save(goal)
        return goal

    def cancel(self) -> Goal:
        goal = self._require_current()
        goal.status = "cancelled"
        self.store().save(goal)
        return goal

    def _require_current(self) -> Goal:
        goal = self.current()
        if goal is None:
            raise ValueError("No goal exists in this session")
        return goal

    def _run(self, store: GoalStore, goal: Goal) -> Goal:
        prepare = PrepareGoalCycleNode(store, self.agent.max_turns, self.checkpoint)
        agent_flow = create_agent_flow(self.agent.provider, self.agent.tools, self.agent.compactor,
                                       self.event_sink, self.cancel_check)
        verify = VerifyGoalNode(
            AcceptanceVerifier(self.agent.tools),
            store,
            self.checkpoint,
        )
        finish = FinishGoalNode()
        prepare >> agent_flow >> verify
        verify.next(prepare, "continue")
        verify.next(finish, "finish")
        flow = Flow(start=prepare)
        try:
            flow.run({
                "goal": goal,
                "session": self.agent.store,
            })
        except KeyboardInterrupt:
            goal.status = "paused"
            goal.last_error = "Interrupted by user"
            store.save(goal)
            raise
        except Exception as error:
            goal.status = "paused"
            goal.last_error = str(error)
            store.save(goal)
            raise
        return goal
