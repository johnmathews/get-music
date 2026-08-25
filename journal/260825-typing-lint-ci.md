# 2026-08-25 — Typing fix, exception narrowing, lint and CI

## Summary

Brought the project up to the global standards: Python 3.13 only, `ruff` + `pyright` configured in `pyproject.toml`,
pyright at 0 errors, ruff clean, formatted, and a GitHub Actions workflow that runs all of it. Tests: 382 → 384
(two new tests for the `OSError` branches), coverage unchanged at 96%.

## Tooling

- `requires-python = ">=3.13"`; `ruff`, `pyright`, `pytest-cov` added to the `dev` group.
- `[tool.ruff]`: line length 120, `py313`, rules `E F I UP B SIM DTZ BLE PL RUF S110`, ignoring the PLR
  complexity/magic-number rules. `tests/*` additionally ignores `S`, `PLR` and `PLC0415` (tests import lazily inside
  test functions on purpose — 58 sites; not worth the churn).
- `[tool.pyright]`: standard mode over `gm/`, venv-aware. `reportPrivateImportUsage = false` because mutagen ships
  `py.typed` but re-exports `File`, `FileType` and `MutagenError` from `_file`/`_util` without an `__all__`, so pyright
  wrongly flagged its documented public API (4 of the 16 baseline errors).
- Machine quirk worth remembering: run `uv run python -m pytest`, not bare `uv run pytest` (a pyenv-global pytest can
  shadow the venv one), and run ruff/pyright via `uv run` so `mutagen` resolves.

## The pyright errors in `gm/metadata.py`

`_prompt_field()` returned `str | object` because the "go back" sentinel was `_BACK = object()`. Every caller then
did `artist = val  # type: ignore[assignment]` and pyright (rightly) complained when the widened value was passed to
`_apply_suggestion(user_input: str)`, `_prompt_field(default: str)` and `AudioMetadata.__init__` — 8 errors across
`prompt_metadata`, `prompt_batch_metadata` and `prompt_title_only`.

Fix at the source: the sentinel is now an enum literal —

```python
class _Nav(Enum):
    BACK = auto()


_BACK: Final = _Nav.BACK


def _prompt_field(label: str, default: str) -> str | _Nav: ...
```

`val is _BACK` now narrows `val` to `str` in the else-branch, so all seven `# type: ignore` comments are gone and no
cast is needed anywhere. One more real error: `_first_tag()` accessed `audio.tags.get()` while mutagen declares
`FileType.tags = None`; the tag container is now read into an `Any`-typed local with an explicit `None` check.

## Exception narrowing

Five blind `except Exception` and two `try/except: pass` sites. What each one now catches, and why:

| Site | Before | After |
| --- | --- | --- |
| `metadata.read_metadata` — `mutagen.File()` | `except Exception: audio = None` | `(mutagen.MutagenError, OSError)`; prints `Could not read tags from <file>` and falls back to filename-derived defaults as before |
| `metadata.write_metadata` — `mutagen.File()` | `except Exception: return` | `(mutagen.MutagenError, OSError)`; prints `Could not open file for tagging` |
| `metadata.write_metadata` — `audio.save()` | `except Exception: pass` | `(mutagen.MutagenError, OSError)`; prints `Could not save metadata tags` |
| `files.embed_cover_art` — `_embed_*()` | `except Exception: pass` | `(mutagen.MutagenError, OSError)`; prints `Could not embed cover art` (the loose `cover.jpg` is still copied) |
| `files.handle_directory` — per-file loop | `except Exception as exc` | `_PER_FILE_ERRORS = (RuntimeError, OSError, subprocess.SubprocessError, sqlite3.Error)` — scp/ssh/ffmpeg failures, local I/O and missing binaries, subprocess timeouts, import-log errors. `EOFError` (stdin closed) and `KeyboardInterrupt` now abort the batch instead of being reported once per remaining file |
| `files.run_ffmpeg` — `int(total_size)` | `try/except ValueError: pass` | `contextlib.suppress(ValueError)` — cosmetic progress-bar stat, silence is the intended UX |
| `history._get_connection` — migrations | `try/except OperationalError: pass` | `contextlib.suppress(sqlite3.OperationalError)` — "column already exists" is the expected steady state |

Why `MutagenError` + `OSError`: every format-specific mutagen error (`ID3Error`, `MP4Error`, `FLACError`, `OggError`,
…) derives from `mutagen.MutagenError`, and file open/read/write failures surface as `OSError` (mutagen re-raises
`IOError` from `File()`). The per-tag `except (KeyError, mutagen.MutagenError): pass` in `write_metadata` was already
specific and stays silent — a missing tag on delete or an unsupported easy-tag key is not a failure.

Before each write site printed nothing on failure, so `Writing metadata...` followed by a clean transfer could hide a
file that never got tagged. The happy path is unchanged.

### Test changes

Three tests asserted that *any* `Exception` was swallowed (`side_effect=Exception("...")`). Those were asserting
silent suppression of bugs, so they now raise `mutagen.MutagenError` and additionally assert the warning is printed;
two sibling tests cover the `PermissionError`/`OSError` branch. The `test_history` fixture was updated for the
`_ConnectionCache` holder (see below) and now closes the cached connection on teardown, which removes the
`ResourceWarning: unclosed database` noise.

## Other lint fixes

- `history`: the two `global` statements replaced by a small `_ConnectionCache` dataclass holder (same single-entry
  semantics).
- `cli`: lazy `from gm.history import …` / `from gm.metadata import …` moved to top-level as `from gm import history,
  metadata` with attribute access, so the existing `patch("gm.history.recent_imports")`-style tests keep working.
- `files`: mutagen submodule imports hoisted to module level; `for line in …: line = line.strip()` renamed.
- `youtube`: `import re` hoisted; one over-long diagnostic line wrapped.
- `ssh`/`metadata`/`files`: list concatenation → unpacking (`RUF005`); `datetime.timezone.utc` → `datetime.UTC`.
- Import ordering and unused imports auto-fixed (23 items), then `ruff format` over the tree (14 files).
- Return annotations: the task expected five unannotated functions in `gm/`; an AST scan found none — every
  function already had a return annotation.

## CI

`.github/workflows/test.yml` — on `push` and `pull_request`: `astral-sh/setup-uv@v6` (cache enabled) → `uv sync` →
`ruff check .` → `ruff format --check .` → `pyright` → `python -m pytest --cov=gm`.

## Docs

`docs/usage.md` drift fixed against `gm help` and the prompt code: prompt headers now show the `< to go back` hint,
the field order is artist → title → album → date (album was missing from the YouTube example and mis-ordered in the
per-file example), the suggestion prompt has no quotes around the match, Python 3.13+, and a note on best-effort tag
writing plus which errors the batch loop tolerates. `CLAUDE.md` gained the test/lint/type-check commands and the
exception-handling convention.
