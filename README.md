# codewatch

A live terminal view of the files in a directory as they are written or changed.
It is meant for watching a project while an editor, a build, or a coding agent
modifies it.

The left pane lists the changes, newest first, with the time, the kind of change
(`+` added, `~` modified, `x` deleted), the path, and the number of lines added
and removed. The right pane shows the file after the change, syntax-highlighted,
with the changed lines marked, or the unified diff of that change.

## Install

With [uv](https://docs.astral.sh/uv/):

```sh
uv tool install git+https://github.com/fatihbozdag/codewatch
```

or with pipx:

```sh
pipx install git+https://github.com/fatihbozdag/codewatch
```

Python 3.11 or later is required.

## Use

```sh
codewatch                     # watch the current directory
codewatch path/to/project     # watch another directory
codewatch . --ignore build dist
codewatch . --include-data    # also watch top-level data/ and results/
```

| Key | Action |
|---|---|
| `f` | follow the newest change |
| `d` | switch between the file view and the diff view |
| `q` | quit |

Selecting an entry in the left pane shows that change and pauses following;
`f` resumes it.

## What is watched

All text files under the root, up to 2 MB each, except:

- version-control, cache and editor directories (`.git`, `__pycache__`,
  `node_modules`, `.mypy_cache`, `.ruff_cache`, `.pytest_cache`,
  `.ipynb_checkpoints`, `.idea`, `.vscode`) and virtual environments (`.venv*`);
- the top-level `data/` and `results/` directories, unless `--include-data` is
  given;
- swap, temporary, lock and compiled files.

The last 300 changes are kept.

## Following files that are read, not changed

`--focus FILE` names a file whose appended lines are paths to show, in the
order they arrive, each optionally preceded by a tag and a tab:

```
review<TAB>src/app.py
tests/test_app.py
```

These entries are marked `>` with their tag, so a process that reads files (for
example a code review) can be followed alongside the changes. When `--focus` is
not given and `~/.codewatch/audit.focus` exists, that file is used.

## License

MIT
