# Code Review — commit a9cc4c8 (WAL retry + SSH PATH resolution on Windows)

Range reviewed: `19e7a75..a9cc4c8` (`git diff 19e7a75786d8a46d098d8af155cfc4ee7cd95d6a..a9cc4c833a82c1d94a7d10e14ba675e6fb712247`)

## Scope
- Production: `infobar/plataforma.py` (new `resuelve_programa`), `infobar/restauracion.py` (`_borra_wal` retry), `infobar/servidores_alta.py`, `infobar/servidores_ssh.py`, `infobar/servidores_externo.py` (3 call sites).
- Tests: `tests/aislamiento.py` (new `BASH`/`ruta_bash`), `tests/test_restauracion.py`, `tests/test_servidores_alta.py`, `tests/test_nan_y_opencode.py`, `tests/test_ingestor.py`, `tests/test_revision_copias.py`, plus 10 smaller NTFS-permission-skip files.
- 22 files, +230/-57.

## Overall Assessment
The `_borra_wal` retry logic is sound and correctly bounded (verified below). The `nan_y_opencode` mtime fix is a clean, deterministic root-cause fix. Most `skipIf(os.name == "nt", ...)` additions are genuinely platform-semantics limits, not laziness (spot-checked, see below). However, two issues need attention before calling this fully done:

1. **`plataforma.resuelve_programa` is unconditionally active in production on every platform (including POSIX) and introduces a real PATH-planting / executable-substitution vector that did not exist before this commit** — confirmed empirically, not just by reading.
2. **The new regression test `test_un_lector_transitorio_no_aborta_la_restauracion` has no platform guard and will fail on POSIX** (macOS/Linux) — confirmed by code-flow analysis and corroborated by the commit's own comments about POSIX unlink semantics.

## Critical

### 1. `resuelve_programa`'s `.py`-sidecar lookup is a genuine, unconditional PATH-planting vector (not test/Windows-gated)

`infobar/plataforma.py:102-117`:
```python
def resuelve_programa(orden):
    for d in os.environ.get("PATH", "").split(os.pathsep):
        candidato = os.path.join(d, orden[0] + ".py")
        if os.path.isfile(candidato):
            return [sys.executable, candidato, *orden[1:]]
    ruta = shutil.which(orden[0])
    return [ruta, *orden[1:]] if ruta else orden
```

The docstring says this exists "para sustituir el programa por uno de pega en los tests" (to substitute the program with a fake one in tests) — but the function itself has **no gate at all**: no `os.name == "nt"` check, no test-only flag, no restriction to a test-controlled directory list. It runs on every `_ejecuta`/`ejecutar`/`host_de_ssh` call, on every platform, in production.

Consequence: it scans the **entire** `PATH`, in order, looking for a file literally named `<orden[0]>.py` (e.g. `ssh.py`), and — if found **anywhere** — prefers it over the real binary, with no regard for where the real binary sits in `PATH`. This is worse than ordinary PATH-precedence attacks, where an attacker needs to win a directory earlier than the legitimate one. Here the `.py` scan is a separate pass that runs *before* the real resolution, so an attacker-writable directory *at the very end* of `PATH` still wins.

Verified empirically on this machine (real `ssh` resolves via Git's `ssh.exe`):
```
real ssh resolved by shutil.which: C:\Program Files\Git\usr\bin\ssh.EXE
# attacker dir appended at the END of PATH, after the real ssh's dir
resuelve_programa(['ssh','-V']) -> ['C:\Python314\python.exe', '...\attacker-xxx\ssh.py', '-V']
```
A directory anywhere on `PATH` containing `ssh.py` (or `ssh-keygen.py`/`ssh-keyscan.py`) silently hijacks every `ssh` invocation in this app — including the SSH diagnostic/health-check calls that run periodically against the user's configured servers — executing attacker-controlled code with `sys.executable` (same privileges as the running tray app) instead of real `ssh`. This is **not** Windows-specific: the same scan runs on macOS/Linux too, where it's a wholly new vector (previously, `execvp`-style resolution on POSIX never considered `.py` as a candidate for `ssh` at all).

This doesn't contradict the "no `ssh.py` will ever legitimately exist on a real PATH" claim — the risk isn't legitimate collision, it's **malicious or accidental planting** (many common Windows user-writable locations end up on `PATH`: per-user pip `Scripts`, nvm/volta shims, `%LOCALAPPDATA%\Microsoft\WindowsApps`, etc.). Given this app stores/uses a dedicated SSH key and talks to the user's real servers, this is a meaningful trust-boundary widening, not just a theoretical nit.

**Suggested fix:** don't make this live in production at all. Either:
- Gate the `.py` scan behind an explicit opt-in the tests control (e.g. an env var the test harness sets, or pass the fake-binary directory list explicitly into `resuelve_programa` instead of scanning the live `os.environ["PATH"]`), or
- At minimum, only consider a `.py` sidecar found in a PATH directory that is *earlier than or equal to* where the real binary would otherwise resolve via `shutil.which`, so a lower-priority directory can never override a higher-priority legitimate one.

Separate from the above: switching from "bare name let CreateProcess resolve (.exe only)" to `shutil.which` pre-resolution also means Windows's `PATHEXT` (`.COM;.EXE;.BAT;.CMD;...`) is now honored where previously only `.exe` was. This part looks like the *intended* effect of the fix (to support `.cmd`/`.bat` shims), so I'm not flagging it as a separate issue — just noting it's a secondary, deliberate widening worth being aware of; real OpenSSH on Windows is always `.exe` so it shouldn't matter in practice.

## Important

### 2. New test `test_un_lector_transitorio_no_aborta_la_restauracion` has no platform guard — will fail on POSIX

`tests/test_restauracion.py:146-176`. The test mocks `restauracion.time.sleep` and asserts `len(intentos) >= 2`, relying on `_borra_wal`'s `os.remove()` raising `OSError` (file locked by the still-open `lector` connection) at least twice before the mock's side effect closes `lector` on the 2nd call.

This retry-triggering condition **cannot occur on POSIX**: `os.remove()`/`unlink()` on POSIX never raises because another file descriptor is open on the same file (this is literally what the sibling tests' own new `skipIf` comments in the same diff say: *"no hay unlink-con-fd-abierto como en POSIX"*, line 97). On POSIX, `_borra_wal`'s first `os.remove()` attempt succeeds immediately, `break`s out of the loop, and `restauracion.time.sleep` is never called — `intentos` stays empty, so `self.assertGreaterEqual(len(intentos), 2)` fails (`0 >= 2`).

Confirmed on this Windows box that the test currently passes here (ran `tests.test_restauracion` + `tests.test_nan_y_opencode`, 20 passed/2 skipped), which is consistent with this being a Windows-only code path, not a Windows-only *test*. Its sibling tests (`test_el_wal_de_la_base_antigua_no_resucita`, `test_wal_huerfano_con_un_lector_abierto_no_resucita`) correctly recognize this exact POSIX/Windows semantic difference and are `skipIf(os.name == "nt", ...)` — this new test needs the mirror-image guard (it's meaningful only on `nt`), e.g.:
```python
@unittest.skipIf(os.name != "nt", "el reintento de _borra_wal solo entra en juego cuando el unlink "
                  "bloquea con el fd abierto (Windows); en POSIX el primer intento siempre tiene exito")
def test_un_lector_transitorio_no_aborta_la_restauracion(self):
    ...
```
Given the project is primarily developed/run on macOS (per `docs`, `launchctl` references, `Application Support` paths), this would break the suite there. Worth fixing before considering this stage fully "done" for non-Windows regression safety.

## Verified as correct (per acceptance criteria)

- **`_borra_wal` retry does not mask a persistent lock**: control flow re-checked — `time.monotonic() >= limite` is only evaluated *inside* the `except OSError` branch, so a persistent lock still raises `ErrorRestauracion` after the ~2s budget (plus up to one 0.1s overshoot), exactly as before the retry was added; it never swallows a real failure. `FileNotFoundError` is listed before the broader `except OSError`, so it's correctly treated as "already gone, done" rather than retried.
- **`skipIf(os.name == "nt", ...)` spot-checks**: `test_el_wal_de_la_base_antigua_no_resucita` / `test_wal_huerfano_con_un_lector_abierto_no_resucita` (keep a reader/writer connection open for the *entire* call, not a transient lock — genuine Windows limit, not laziness); `test_espera_a_que_la_barra_suelte_el_puesto` in `test_revision_copias.py` (same pattern); `test_base_borrada_con_la_barra_en_marcha_se_recrea` in `test_ingestor.py` (same-process open handle, even stronger case); `os.mkfifo`/`os.symlink` skips (API doesn't exist / needs a privilege Windows doesn't grant by default) — all legitimate. The NTFS 0600/0700 skips correctly go further than "test can't check it" — `test_claves_copias.py` adds an honest comment that the underlying protection is *actually absent* on Windows (chmod can't restrict NTFS read access), not just untestable. Good transparency.
- **No command-injection / attacker-controlled `orden[0]`**: all three call sites (`servidores_alta._ejecuta`, `servidores_ssh.ejecutar`, `servidores_externo.host_de_ssh`) pass a hardcoded `"ssh"` literal as `orden[0]`; only trailing arguments (host, port, remote command string) are variable, and those were already subject to the same construction before this commit. `resuelve_programa` itself doesn't add any new taint path for `orden[0]` beyond the PATH-planting issue above.
- **Full suite**: re-ran `tests.test_restauracion` + `tests.test_nan_y_opencode` on this Windows box — 20 passed, 2 skipped, consistent with the reported full-suite run.

## Minor / Informational

- `_borra_wal` gives each suffix (`-wal`, `-shm`) its own independent 2s budget, so a worst case where both are transiently locked could take up to ~4s total before giving up. Not a bug, just worth knowing it's "2s per file" rather than "2s total."
- `resuelve_programa` does a full `PATH` directory scan (`os.path.isfile` per entry) *plus* a `shutil.which` scan on every call, for every `ssh`/`ssh-keygen`/`ssh-keyscan` invocation. For a periodic SSH health-check loop across several servers this is negligible (tens of stat calls, microseconds), not worth caching given the project's scale — just flagging so it's a conscious non-issue rather than an unconsidered one.
- The `resuelve_programa` docstring's claim that `subprocess` handles `.cmd`/`.bat` "via its own documented special-casing" couldn't be fully confirmed either way without deeper verification, but it's moot for this codebase: every real program resolved here (`ssh`, `ssh-keygen`, `ssh-keyscan`, `taskkill`) is always a real Windows `.exe`, so the `.cmd`/`.bat` branch of `shutil.which`'s result is never actually exercised in practice.

## Edge Cases Found by Scout
Covered by the Critical/Important findings above (PATH-planting via `.py` sidecar; missing POSIX guard on the new regression test). No additional dependents of `resuelve_programa`, `_borra_wal`, or the `aislamiento.BASH`/`ruta_bash` helpers were found beyond the files already listed in scope.

## Recommended Actions
1. Restrict `resuelve_programa`'s `.py`-sidecar lookup so it cannot run unconditionally in production on every platform (critical finding #1).
2. Add the missing platform guard to `test_un_lector_transitorio_no_aborta_la_restauracion` (important finding #2) before considering this stage done — otherwise the next macOS/Linux test run breaks on this specific test.

## Unresolved Questions
- Is there an actual plan to ever run this test suite on macOS/Linux again now that the active development loop is Windows-only for this stage? If not for a while, finding #2 is lower urgency but should still be fixed before it's forgotten.
- Is the `.py`-sidecar mechanism in `resuelve_programa` intended to ever ship to real users' machines, or is it meant to only matter when tests run? If the latter, the cleanest fix is likely to not route it through the live `PATH` scan at all.
