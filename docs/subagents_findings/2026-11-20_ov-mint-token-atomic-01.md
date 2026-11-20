# OV-MINT-TOKEN-ATOMIC-01

Keywords: OV-MINT-TOKEN-ATOMIC-01, dms-377, ManifestMinter, _first_mint, _register, _install_token_file, DMS_OV_SERVICE_TOKEN_FILE, O_TRUNC, mkstemp, os.replace, fsync, temp file, rename, symlink, FIFO, bind mount, in-place fallback, ov_mint_failed, ov_service_token_missing, adversary round

## Main idea

`_first_mint` opened the token file with `O_TRUNC` before `POST /keys/services`. Now nothing a later boot reads is created, truncated or replaced before OpenVault has returned a token. Unit tests mock OpenVault. Not live. Nothing PASS.

## Expected vs actual (main ebe65cc)

- Expected: a failed first mint leaves no token file, and leaves an existing file as it was.
- Actual: an empty 0600 file was left behind. An existing file was truncated and re-moded to 0600. A symlink at the path had its target emptied.

## Repro

`python -m pytest tests/test_ov_mint_token_atomic_01.py -q` with `manifest.py` from main: the register-failure, existing-file and symlink tests fail. One FIFO test blocks on main's `os.open`, so run main with `-k "not fifo"`.

## Root-cause class

A file a later boot trusts was written before the step that makes it valid had succeeded. Rule: prepare beside, validate, then rename. Invariants: "fix the root-cause class", "assert the artifact the user receives" (here the file the second boot reads), "prove a gate can fail" (11 mutants, each red).

## Design

- `tempfile.mkstemp` beside the token file, before the call. 0600 and exclusive from creation. An unwritable directory, or a directory at the path, refuses before OpenVault issues anything.
- `_register` returns a token or raises. It writes nothing. The token must be a string of printable characters with no whitespace.
- Write, flush, fsync, `os.replace`, then fsync the directory (POSIX, best effort). `finally` removes the temp file.
- A rename that is refused (a bind-mounted single file) falls back to an in-place write of an existing regular file, after success only, with a warning. A failed in-place write empties the file.

## Adversary round (three Sonnet agents, isolated context, none wrote the fix)

- Crash lens: two P1 found and fixed. A short in-place write left 8 bytes that the next boot read as a token. A bool, number, list or multi-line token was stored as text.
- Race lens: held. 300 runs of 8 threads, 150 runs of 6 processes. A reader never saw a partial or empty token file on the rename path.
- Filesystem lens: P1 rule held. Fixed: the in-place write could wait forever on a FIFO.

## Open

- A hard kill between the register answer and the rename leaves a 0600 `.name.*.tmp` holding the token. No boot reads it. Recovery is an admin rotate, the same as a lost response. Nothing cleans stale temp files.
- A hard kill inside the in-place write can leave part of a token. Only on the degraded path.
- The in-place write goes through a hard link. A file name within 13 characters of NAME_MAX now refuses before register.
- A BOM-only token file is read as a token. Same on main.
- Two first mints in one process: the loser gets `ov_service_token_missing` once. Same on main.
- The bind-mount case is proven only by patching `os.replace`. No real bind mount, no live OpenVault, no Windows run.
