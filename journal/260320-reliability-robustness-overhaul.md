# Development Journal

## 2026-03-20: Reliability and robustness overhaul

### Context

The tool was experiencing flaky behavior — downloads would hang, metadata would silently go wrong, and files with spaces
in names would fail during transfer. A full code review identified 12 issues across the codebase, all of which were fixed
in a single pass.

### Issues fixed

#### 1. SSH connection multiplexing and timeouts (`gm/ssh.py`)

**Problem:** Every SSH call spawned a fresh TCP+SSH connection. A typical YouTube download makes 10-15 SSH calls, each
paying full handshake latency. No timeouts meant a network blip would hang the tool forever.

**Fix:** Added `ControlMaster=auto`, `ControlPath`, and `ControlPersist=60` options so all SSH calls in a session reuse
one connection. Added `ConnectTimeout=10`, `ServerAliveInterval=15`, and `subprocess.run(timeout=...)` so commands fail
cleanly instead of hanging. Stream mode uses a 10-minute timeout; standard commands use 5 minutes.
`TimeoutExpired` is caught and converted to a failed `CompletedProcess` with a descriptive error.

#### 2. `write_metadata_ssh` stripped embedded thumbnails (`gm/metadata.py`)

**Problem:** The ffmpeg command used `-map 0:a` which copies only audio streams. This silently removed the embedded
thumbnail that yt-dlp had just added. Users would see thumbnails randomly missing in Navidrome.

**Fix:** Changed to `-map 0` to copy all streams (audio + attached pictures). This preserves the embedded thumbnail
through the metadata rewrite step.

#### 3. `scp_transfer` didn't quote remote paths (`gm/files.py`)

**Problem:** `build_scp_command` passed the remote path directly as `{host}:{path}`. SCP interprets shell metacharacters
in the remote portion, so filenames with spaces (which are allowed per the Lidarr convention) would fail.

**Fix:** Remote path is now wrapped with `shlex.quote()`, matching how SSH commands already use `quote_path()`.

#### 4. `prompt_title_only` didn't handle `_BACK` sentinel (`gm/metadata.py`)

**Problem:** `_prompt_field` returns a `_BACK` sentinel object when the user types `<`, but `prompt_title_only` assigned
it directly to `title` without checking. The sentinel object would propagate through `sanitize_filename` and produce
garbage output.

**Fix:** Added a `while True` loop that re-prompts until a non-`_BACK` value is entered, consistent with how
`prompt_metadata` and `prompt_batch_metadata` handle it.

#### 5. Non-deterministic file selection with `find | head -1` (`gm/youtube.py`)

**Problem:** `find` doesn't guarantee ordering. If multiple audio files existed in the temp dir (e.g., from a partial
retry), the wrong file could be picked.

**Fix:** Replaced `find ... | head -1` with `ls -1t ... | head -1` which sorts by modification time (newest first). This
ensures the most recently downloaded file is always selected.

#### 6. `cat *.info.json` with multiple files (`gm/youtube.py`)

**Problem:** If multiple `.info.json` files existed, `cat *.info.json` would concatenate them into invalid JSON.
`parse_ytdlp_metadata` would silently return empty metadata, causing blank artist/title prompts.

**Fix:** Changed to `cat "$(ls -1t *.info.json | head -1)"` to read only the newest info.json file.

#### 7. Orphaned temp directories (`gm/youtube.py`)

**Problem:** If a download was interrupted (Ctrl+C, crash, network drop), the temp directory (`/tmp/gm-download-*`)
would be left on the LXC with no cleanup mechanism.

**Fix:** Added `_cleanup_stale_temp_dirs()` which runs at the start of each YouTube download, removing any
`gm-download-*` directories older than 30 minutes.

#### 8. Database connection churn (`gm/history.py`)

**Problem:** Every database operation opened a new connection and ran schema creation + migrations. During a batch
directory import of 50 files, this meant 50+ connection cycles with repeated `CREATE TABLE IF NOT EXISTS`.

**Fix:** Added connection caching via module-level `_conn_cache`. The connection is created once and reused for all
subsequent calls. The cache is keyed by `DB_PATH` so test fixtures can still swap the database path.

#### 9. Stream mode swallowed stderr (`gm/ssh.py`)

**Problem:** In stream mode, stdout went to `DEVNULL` and the `CompletedProcess` stored empty strings for both stdout
and stderr. When yt-dlp or ffmpeg failed, users got a generic "Download failed" with no diagnostic info.

**Fix:** Stream mode now inherits both stdout and stderr from the parent process (no `DEVNULL` redirect), so error
output is visible in the terminal. The captured stderr is also stored in the `CompletedProcess`.

### Files changed

- `gm/ssh.py` — Complete rewrite: connection multiplexing, timeouts, keep-alive, timeout handling
- `gm/metadata.py` — Fixed `-map 0:a` to `-map 0`; fixed `prompt_title_only` _BACK handling
- `gm/files.py` — Added `shlex` import; quoted remote path in `build_scp_command`
- `gm/youtube.py` — Deterministic file selection; single info.json; temp dir cleanup on startup
- `gm/history.py` — Connection caching; removed redundant `try/finally conn.close()` blocks
- `tests/test_ssh.py` — Rewrote for new SSH options, timeout, multiplexing tests
- `tests/test_youtube.py` — Added `_cleanup_stale_temp_dirs` mock to all `handle_youtube` tests; new cleanup test
- `tests/test_metadata.py` — Updated `write_metadata_ssh` test for `-map 0`; added `prompt_title_only` _BACK test
- `tests/test_files.py` — Updated `build_scp_command` test for quoting; added spaces-in-path test
- `tests/test_history.py` — Reset connection cache in test fixture
- `docs/usage.md` — Documented SSH connection management and temp dir cleanup
- `CLAUDE.md` — Updated ssh.py description
