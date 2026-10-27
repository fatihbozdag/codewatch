"""codewatch: a live terminal view of files as they are written or changed.

Left pane: the changes, newest first (time, kind, path, lines added/removed).
Right pane: the file after the change, syntax-highlighted, with the changed
lines marked; `d` switches to the unified diff of that change.

Keys: f follow the newest change, d file/diff view, q quit.

--focus FILE also shows, in arrival order, every file named on a line appended
to FILE ("tag<TAB>path"; marked > and the tag), so files that are read
rather than changed (an audit in progress) can be followed too.

Usage: codewatch [ROOT] [--ignore DIR ...] [--include-data] [--focus FILE]
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from rich.syntax import Syntax
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static
from watchfiles import Change, awatch

IGNORE_ANYWHERE = {".git", "__pycache__", "node_modules", ".mypy_cache", ".ruff_cache",
                   ".pytest_cache", ".ipynb_checkpoints", ".idea", ".vscode"}
IGNORE_TOP = {"data", "results"}          # large corpus/output trees at the root
IGNORE_SUFFIXES = (".swp", ".swx", ".pyc", ".DS_Store", "~", ".tmp", ".lock")
MAX_BYTES = 2_000_000
MAX_SNAPSHOT_FILES = 20_000
MAX_HISTORY = 300
KIND = {Change.added: ("+", "green"), Change.modified: ("~", "yellow"), Change.deleted: ("x", "red")}
VIEWED = ">"   # pseudo-kind: a file named in the focus file, shown without a change
DEFAULT_FOCUS = Path("~/.codewatch/audit.focus").expanduser()   # used when it exists and --focus is not given


@dataclass
class Event:
    path: Path
    when: float
    kind: Change | str
    text: str | None = None
    tag: str = ""
    diff: str = ""
    changed: set[int] = field(default_factory=set)
    added: int = 0
    removed: int = 0


class Watcher:
    """Keeps the last seen text of every file and turns changes into Events."""

    def __init__(self, root: Path, ignore: set[str], include_data: bool):
        self.root = root
        self.ignore = IGNORE_ANYWHERE | ignore
        self.ignore_top = set() if include_data else IGNORE_TOP
        self.snapshots: dict[Path, list[str]] = {}

    def wanted(self, path: Path) -> bool:
        try:
            parts = path.relative_to(self.root).parts
        except ValueError:
            return False
        if not parts or (parts[0] in self.ignore_top and len(parts) > 1):
            return False
        if any(p in self.ignore or p.startswith(".venv") for p in parts):
            return False
        return not path.name.endswith(IGNORE_SUFFIXES)

    def read(self, path: Path) -> str | None:
        try:
            if path.stat().st_size > MAX_BYTES:
                return None
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def snapshot_all(self) -> int:
        for dirpath, dirnames, filenames in os.walk(self.root):
            here = Path(dirpath)
            dirnames[:] = [d for d in dirnames if self.wanted(here / d / "x")]
            for name in filenames:
                path = here / name
                if len(self.snapshots) >= MAX_SNAPSHOT_FILES:
                    return len(self.snapshots)
                if self.wanted(path) and (text := self.read(path)) is not None:
                    self.snapshots[path] = text.splitlines(keepends=True)
        return len(self.snapshots)

    def event(self, kind: Change, path: Path) -> Event | None:
        if kind == Change.deleted:
            return Event(path, time.time(), kind)   # snapshot kept: an atomic save re-adds the file
        text = self.read(path)
        if text is None:
            return None
        new = text.splitlines(keepends=True)
        old = self.snapshots.get(path, [])
        if new == old:
            return None
        self.snapshots[path] = new
        ev = Event(path, time.time(), Change.added if not old else Change.modified, text)
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
            if tag in ("replace", "insert"):
                ev.changed.update(range(j1 + 1, j2 + 1))
                ev.added += j2 - j1
            if tag in ("replace", "delete"):
                ev.removed += i2 - i1
        rel = str(path.relative_to(self.root))
        ev.diff = "".join(difflib.unified_diff(old, new, f"a/{rel}", f"b/{rel}", n=3))
        return ev


class CodeWatch(App):
    CSS = """
    #changes { width: 46; border-right: solid $primary; }
    #view { width: 1fr; }
    #status { height: 1; background: $boost; padding: 0 1; }
    """
    BINDINGS = [("f", "follow", "Follow newest"), ("d", "toggle_diff", "File / diff"),
                ("q", "quit", "Quit")]

    def __init__(self, watcher: Watcher, focus: Path | None = None):
        super().__init__()
        self.watcher = watcher
        self.focus = focus
        self.focus_offset = 0
        self.events: list[Event] = []
        self.shown: Event | None = None
        self.follow = True
        self.diff_mode = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            yield ListView(id="changes")
            with VerticalScroll(id="view"):
                yield Static("Indexing files ...", id="code")
        yield Static("", id="status")
        yield Footer()

    async def on_mount(self) -> None:
        self.title = "codewatch"
        n = await asyncio.to_thread(self.watcher.snapshot_all)
        self.query_one("#code", Static).update(
            f"Watching {self.watcher.root}\n{n} text files indexed. Waiting for changes ...")
        self.set_status()
        self.run_worker(self.watch(), exclusive=True)

    def _is_focus(self, raw: str) -> bool:
        return self.focus is not None and Path(raw) == self.focus

    async def watch(self) -> None:
        paths = [self.watcher.root]
        if self.focus is not None:
            self.focus.parent.mkdir(parents=True, exist_ok=True)
            self.focus.touch()
            self.read_focus()   # replay lines written before start, in order
            paths.append(self.focus.parent)
        async for changes in awatch(*paths, debounce=150, step=50,
                                    watch_filter=lambda _c, p: self._is_focus(p)
                                    or self.watcher.wanted(Path(p))):
            for kind, raw in sorted(changes, key=lambda c: c[1]):
                if self._is_focus(raw):
                    self.read_focus()
                    continue
                ev = self.watcher.event(kind, Path(raw))
                if ev is not None:
                    self.add_event(ev)

    def read_focus(self) -> None:
        """Show each file named on a line appended to the focus file ("tag<TAB>path" or "path")."""
        try:
            with self.focus.open(encoding="utf-8") as fh:
                fh.seek(self.focus_offset)
                chunk = fh.read()
        except OSError:
            return
        complete, _, _ = chunk.rpartition("\n")
        if not complete:
            return
        self.focus_offset += len(complete.encode("utf-8")) + 1
        for line in complete.splitlines():
            tag, _, raw = line.strip().rpartition("\t")
            if not raw:
                continue
            path = Path(raw).expanduser()
            path = (path if path.is_absolute() else self.watcher.root / path).resolve()
            text = self.watcher.read(path)
            if text is not None:
                self.add_event(Event(path, time.time(), VIEWED, text, tag=tag))

    def add_event(self, ev: Event) -> None:
        self.events.insert(0, ev)
        stamp = (time.strftime("%H:%M:%S", time.localtime(ev.when)), "dim")
        if ev.kind == VIEWED:
            label = Text.assemble(stamp, " ", (VIEWED, "bold cyan"), " ",
                                  (f"[{ev.tag}] " if ev.tag else "", "cyan"), self.rel(ev.path))
        else:
            symbol, colour = KIND[ev.kind]
            label = Text.assemble(stamp, " ", (symbol, f"bold {colour}"), " ", self.rel(ev.path), " ",
                                  (f"+{ev.added}", "green"), (f" -{ev.removed}", "red"))
        changes = self.query_one("#changes", ListView)
        changes.insert(0, [ListItem(Label(label))])
        if len(self.events) > MAX_HISTORY:
            self.events.pop()
            changes.children[-1].remove()
        if self.follow:
            self.show(ev)

    def rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.watcher.root))
        except ValueError:
            return str(path)

    def show(self, ev: Event) -> None:
        self.shown = ev
        code = self.query_one("#code", Static)
        view = self.query_one("#view", VerticalScroll)
        if ev.kind == Change.deleted:
            code.update(Text(f"{ev.path} was deleted.", style="red"))
        elif self.diff_mode and ev.kind != VIEWED:
            code.update(Syntax(ev.diff or "(no textual difference)", "diff", theme="monokai"))
            view.scroll_home(animate=False)
        else:
            lexer = Syntax.guess_lexer(str(ev.path), ev.text)
            code.update(Syntax(ev.text, lexer, theme="monokai", line_numbers=True,
                               highlight_lines=ev.changed, word_wrap=False))
            first = min(ev.changed) if ev.changed else 1
            view.scroll_to(y=max(first - 6, 0), animate=False)
        self.set_status()

    def set_status(self) -> None:
        mode = "diff" if self.diff_mode else "file"
        follow = "following newest" if self.follow else "paused (f to follow)"
        shown = self.rel(self.shown.path) if self.shown else "-"
        self.query_one("#status", Static).update(
            f"{len(self.events)} changes | {mode} view | {follow} | {shown}")

    def on_list_view_selected(self, message: ListView.Selected) -> None:
        index = message.list_view.index
        if index is not None and index < len(self.events):
            self.follow = index == 0
            self.show(self.events[index])

    def action_follow(self) -> None:
        self.follow = True
        if self.events:
            self.query_one("#changes", ListView).index = 0
            self.show(self.events[0])
        self.set_status()

    def action_toggle_diff(self) -> None:
        self.diff_mode = not self.diff_mode
        if self.shown:
            self.show(self.shown)
        self.set_status()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="codewatch", description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--ignore", nargs="*", default=[], help="further directory names to skip")
    ap.add_argument("--include-data", action="store_true",
                    help="also watch top-level data/ and results/")
    ap.add_argument("--focus", default=None,
                    help="file whose appended lines ('tag<TAB>path') name files to show, in order")
    args = ap.parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    focus = Path(args.focus).expanduser().resolve() if args.focus else None
    if focus is None and DEFAULT_FOCUS.exists():
        focus = DEFAULT_FOCUS
    CodeWatch(Watcher(root, set(args.ignore), args.include_data), focus).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
