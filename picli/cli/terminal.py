"""Interactive terminal input and streamed Markdown rendering."""

import sys
from collections.abc import Callable, Iterable
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.text import Text


BASE_COMPLETIONS = (
    "/help",
    "/new",
    "/sessions",
    "/resume",
    "/tree",
    "/checkout",
    "/fork",
    "/model",
    "/status",
    "/doctor",
    "/config",
    "/tools",
    "/history",
    "/memory",
    "/memory global",
    "/memory session",
    "/skills",
    "/skill",
    "/permissions",
    "/permissions clear",
    "/permissions read-only",
    "/permissions workspace-write",
    "/permissions danger-full-access",
    "/context",
    "/compact",
    "/goal",
    "/goal status",
    "/goal resume",
    "/goal criteria",
    "/goal approve",
    "/goal pause",
    "/goal cancel",
    "/exit",
)


def is_interactive_terminal() -> bool:
    return bool(
        getattr(sys.stdin, "isatty", lambda: False)()
        and getattr(sys.stdout, "isatty", lambda: False)()
    )


class PrefixCompleter(Completer):
    """Complete whole slash-command lines from a dynamic candidate source."""

    def __init__(self, candidates: Callable[[], Iterable[str]]):
        self.candidates = candidates

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        for candidate in sorted(set(self.candidates())):
            if candidate.startswith(text) and candidate != text:
                yield Completion(candidate, start_position=-len(text))


class TerminalSession:
    """Persistent line editor with multiline input and slash completion."""

    def __init__(
        self,
        history_path: Path,
        candidates: Callable[[], Iterable[str]],
        status: Callable[[], str],
    ):
        history_path.parent.mkdir(parents=True, exist_ok=True)
        bindings = KeyBindings()

        @bindings.add("enter")
        def submit(event) -> None:
            event.current_buffer.validate_and_handle()

        @bindings.add("c-j")
        @bindings.add("escape", "enter")
        def newline(event) -> None:
            event.current_buffer.insert_text("\n")

        @bindings.add("c-c")
        def cancel_or_exit(event) -> None:
            if event.current_buffer.text:
                event.current_buffer.reset()
            else:
                event.app.exit(result=None)

        self.status = status
        self.session: PromptSession[str] | None = None
        if is_interactive_terminal():
            self.session = PromptSession(
                history=FileHistory(str(history_path)),
                auto_suggest=AutoSuggestFromHistory(),
                completer=PrefixCompleter(candidates),
                complete_while_typing=False,
                key_bindings=bindings,
                multiline=True,
                style=Style.from_dict(
                    {
                        "prompt": "bold ansicyan",
                        "continuation": "ansibrightblack",
                        "bottom-toolbar": "bg:#222222 #aaaaaa",
                    }
                ),
            )

    def read(self) -> str | None:
        if self.session is None:
            try:
                return input("\nyou> ")
            except EOFError:
                return None
        try:
            return self.session.prompt(
                HTML("<prompt>\nyou&gt; </prompt>"),
                prompt_continuation=HTML("<continuation>...  </continuation>"),
                bottom_toolbar=lambda: self.status(),
            )
        except EOFError:
            return None


class StreamingMarkdownRenderer:
    """Render a live Markdown response in a TTY and plain text elsewhere."""

    def __init__(self, interactive: bool | None = None):
        self.interactive = is_interactive_terminal() if interactive is None else interactive
        self.parts: list[str] = []
        self.live: Live | None = None
        self.console: Console | None = None
        self.plain_started = False

    @property
    def has_text(self) -> bool:
        return bool(self.parts)

    def start(self) -> None:
        if not self.interactive:
            return
        self.console = Console(file=sys.stdout, force_terminal=True)
        self.live = Live(
            Group(Text("assistant> ", style="bold cyan"), Spinner("dots", text="等待模型响应")),
            console=self.console,
            refresh_per_second=12,
            transient=True,
        )
        self.live.start(refresh=True)

    def write(self, fragment: str) -> None:
        self.parts.append(fragment)
        if self.live is not None:
            self.live.update(
                Group(Text("assistant> ", style="bold cyan"), Markdown("".join(self.parts))),
                refresh=True,
            )
            return
        if not self.plain_started:
            print("\nassistant> ", end="", flush=True)
            self.plain_started = True
        print(fragment, end="", flush=True)

    def finish(self) -> None:
        if self.live is not None:
            self.live.stop()
            self.live = None
            if self.parts and self.console is not None:
                self.console.print(
                    Group(Text("assistant> ", style="bold cyan"), Markdown("".join(self.parts)))
                )
        elif self.plain_started:
            print(flush=True)
