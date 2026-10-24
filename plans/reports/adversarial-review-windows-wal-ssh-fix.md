# Adversarial review: Windows WAL retry and ssh resolution (stage 3)

Range: `git diff 19e7a75` (working tree, includes the uncommitted `resuelve_programa` fix on top of a9cc4c8).
Env: Windows 11, Python 3.14.2. Tests run: `tests.test_restauracion` + `tests.test_servidores_alta`: 56 OK, 2 skipped.
**Caveat:** this agent shell has `NoDefaultCurrentDirectoryInExePath=1` set, which hides finding A1. A normal user shell does not set it.

## Verdict

**No Critical (blocking) findings.** Two Medium findings should be fixed before merge (A1, A2). They share one fix. One more is Low (A3). The rest held up when attacked.

The original fix works for the scenario it was written for. A `.py` placed in a PATH directory that comes after the real binary's directory is never reached. But the fix still relies on `shutil.which`, and on Windows that brings in the current working directory (CWD) and every PATHEXT extension. That opens two paths the first review did not see.

## Findings

### A1. On Windows, `shutil.which(nombre, path=d)` puts the CWD first on every call, so a CWD `ssh.cmd`/`.bat`/`.exe` beats the real ssh. Medium, **Accept**
- CPython 3.12+ `shutil.which` on win32 runs `path.insert(0, os.curdir)` whenever `_winapi.NeedCurrentDirectoryForExePath(cmd)` is true. That is the default unless the env var `NoDefaultCurrentDirectoryInExePath` is set. Passing `path=d` does not turn this off.
- So the first loop iteration already checks `.\ssh.{COM,EXE,BAT,CMD,...}` before PATH directory 0. It even checks it before d0's `.py` sidecar.
- Reproduced (env var removed, CWD = a scratch dir containing `ssh-keyscan.cmd`): `resuelve_programa(['ssh-keyscan',...])` returned `['.\\ssh-keyscan.CMD', ...]` (a relative path).
- What changed: before this commit, bare `"ssh"` went to CreateProcess. That also searched the CWD, but **only for `ssh.exe`**. CWD `.cmd`/`.bat` is a new hijack path.
- Exposure in production: `bandeja_windows.py:183` runs `os.chdir(costbar.AQUI)`, so the tray's CWD is the app's own directory. Writing there already means changing the app's code, so for the tray this is not a privilege gain. Any other entry point (CLI, dev runs, `barra.py`/`popover.py` started by hand) inherits whatever CWD it was started from, for example a cloned repo or Downloads. That is classic CWE-427 binary planting.

### A2. `.cmd`/`.bat` results bring back the cmd.exe re-parsing ("wheel") bug. Medium, **Accept**
- The docstring still lists `.cmd`/`.bat` resolution as an intended feature. The first review dismissed it as "never exercised". It can be reached through A1, or through any `ssh.cmd`/`ssh.bat` in a PATH directory earlier than the real ssh (even an honest wrapper from a package manager).
- Python does not escape arguments for cmd.exe (it chose not to patch the BatBadBut class). Reproduced with a harmless `ssh.cmd` (`@echo ARGS: %*`) and the real `servidores_alta.DIAGNOSTICO` as the remote command:
  ```
  RESOLVED .\ssh.CMD
  ERR "wheel" no se reconoce como un comando interno o externo
  ```
  This is the exact bug that motivated dropping the `.cmd` fakes. cmd.exe splits on the `|`/`&&` inside the remote script and **runs the fragments locally** (`tr`, `grep`, `sudo -n true 2>/dev/null`, redirections). Windows 11 ships its own `sudo.exe`.
- User-controlled fields cannot inject anything: `servidores._SSH`, `alta._HOST`, `_USUARIO` and `_CLAVE_PUBLICA` are strict allowlists with no `&|<>^"%`. The problem is the fixed remote strings, which are full of shell metacharacters.
- **Fix for A1 and A2 together.** Stop using `shutil.which`. Resolve by hand, only in absolute directories, only with `.exe`/`.com` on Windows:
  ```python
  _EXT = (".exe", ".com") if os.name == "nt" else ("",)
  def resuelve_programa(orden):
      nombre = orden[0]
      for d in os.environ.get("PATH", "").split(os.pathsep):
          if not d or not os.path.isabs(d):
              continue                                  # "" / "." / relativos = CWD: nunca
          if _SIDECAR_PY and os.path.isfile(os.path.join(d, nombre + ".py")):
              return [sys.executable, os.path.join(d, nombre + ".py"), *orden[1:]]
          for ext in _EXT:
              ruta = os.path.join(d, nombre + ext)
              if os.path.isfile(ruta) and os.access(ruta, os.X_OK):
                  return [ruta, *orden[1:]]
      return orden
  ```
- `_SIDECAR_PY` should be a test-only opt-in, for example `os.environ.get("COSTGUARD_PROGRAMAS_PY") == "1"`, set in `tests/aislamiento.py`. That removes the test hook from production entirely, which the stage-1 review also recommended. A same-user env var is not a privilege boundary.
- Optionally, return `orden` only as a last-resort fallback. Even then, bare-name CreateProcess still searches the CWD for `.exe`, but that was already the case before this commit.

### A3. Empty or relative PATH entries make the `.py` sidecar resolve in the CWD. Low, **Accept** (fixed by the `isabs` check above)
- `os.path.join("", "ssh.py") == "ssh.py"`, so `isfile` is checked against the CWD. `shutil.which(..., path="")` returns None, so binaries are handled differently from the sidecar.
- Reproduced: with a leading `;` in PATH and `ssh.py` in the CWD, the call returned `['C:\\Python314\\python.exe', 'ssh.py', ...]`. A relative path is then launched against the CWD.
- This machine's PATH has no empty entries today (78 entries). Trailing or doubled `;` is common on Windows, and `::` on POSIX. On POSIX this is the only CWD path, since `which` does not add the CWD there.

### A4. The PATHEXT tail (`.VBS/.JS/.WSF/.PY/.MSC/.CPL`) can be returned. Low, **Defer** (also fixed by A2's fix)
`which` can return `ssh.vbs` from an earlier directory, and CreateProcess cannot run it. The result is `OSError`, then "no se pudo lanzar ssh" / `codigo None`. It only causes a denial of service, not code execution. `.PY` from PATHEXT is caught earlier by the sidecar check in the same directory.

### A5. Threat model: a compromised PATH. **Reject as out of scope**
Anything that can prepend a directory to this user's PATH or change PATHEXT is the same user. It can already edit the app's code in `AQUI`, `~/.ssh/infobar_ed25519`, `config.json` and so on. That is not a privilege boundary. What matters is untrusted *content* ending up in a place we search (the CWD, a downloaded folder) and honest-but-hostile file types (`.cmd`). That is why A1 to A3 count and A5 does not.

### A6. Things checked in the original fix that held. **Reject (sound)**
- Planted `.py` after the real binary's directory: never reached, because the loop returns at the first directory that has either one. Re-verified.
- Same directory holding both `ssh.py` and `ssh.exe`: `.py` wins. Planting there needs write access to the real binary's directory, which is equivalent to replacing `ssh.exe`. Out of scope.
- Directories that don't exist, trailing slashes, malformed entries: `isfile` and `which` both return false/None and the loop moves on. Tested with `C:\nonexistent` first.
- Case: NTFS `isfile` ignores case, and `which` uses `normcase`. Symlinked or junction directories are followed like any other PATH directory, with no new exposure.
- TOCTOU between `isfile` and launch: whoever can swap the file between the check and the launch can write to that directory, which is already game over. The race adds nothing.

### A7. Argument smuggling and shell re-joining. **Reject**
`resuelve_programa` only replaces `orden[0]`. None of the three call sites passes `shell=True`. There is no `list2cmdline`, `os.system` or `" ".join(orden)` anywhere in `infobar/`. Destinations are allowlisted and placed after `--`. With `.exe` targets, the arguments reach ssh untouched. The only re-parsing path left is A2.

### A8. `_borra_wal` retry: DoS and spinning. **Reject** (strictly better than before)
- No hot loop. Success breaks out, `FileNotFoundError` breaks out, and any other `OSError` sleeps 0.1 s before retrying. The deadline is only checked on the `OSError` branch, which always sleeps. The longest possible delay is 2 s per suffix, about 4 s in total.
- A hostile local process holding `-wal` open: before the change this failed immediately, now it fails after at most 4 s, with the same message and nothing changed. Such a process is the same user and could just as easily hold the database or delete it. No amplification.
- The real transient lockers on Windows are antivirus, the indexer and OneDrive. The retry suits them well.
- Corruption check on the post-replace call (`tras_reemplazar=True`): SQLite on win32 opens files without `FILE_SHARE_DELETE`, so `os.replace` cannot succeed while any connection holds the old database. So a `-wal` that exists after a successful replace belongs to the *new* database, not a stale one. A transient holder closing it checkpoints its own valid WAL, and the `FileNotFoundError` branch is then correct. The retry cannot turn a stale-WAL resurrection into a silent success.
- Pre-replace call: a transient reader that closes checkpoints the old WAL into the *old* database, which is then replaced. Harmless.
- Minor and pre-existing: if a non-cooperating process keeps the new database's WAL open, the error says "bórralo a mano". Deleting a live WAL by hand could lose that process's writes. **Defer** (could reword to "cierra lo que la use").

### A9. Test-only `BASH`/`ruta_bash`. **Reject**
The only references are in `tests/` (`aislamiento.py`, `test_servidores_alta.py`, `test_servidores_recursos.py`, `test_servidores_v2.py`, `test_servidores_webs.py`). Production never embeds local filesystem paths in shell text. The only text production splices into a shell is the regex-validated public key in `comando_instalacion`, and that runs on the remote side. Unrelated to A2.

### A10. Test hygiene. Low, **Defer**
- `mock.patch.object(restauracion.time, "sleep", ...)` patches `time.sleep` for the whole process, including other threads while the test runs. It works here, but `mock.patch("infobar.restauracion.time.sleep")` has the same effect. A narrower option is a module-level `_dormir = time.sleep` seam.
- `test_un_lector_transitorio_no_aborta_la_restauracion` repeats the same `assertEqual(claves_de(...))` twice in a row.
- The `NoDefaultCurrentDirectoryInExePath=1` variable in agent shells hides A1 from the test suite. Add a regression test that drops the variable, chdirs into a temp directory holding `ssh.cmd`, and asserts the result is an absolute `.exe` path, or `orden` unchanged.

### A11. Observability. Informational
When nothing resolves, `resuelve_programa` returns `orden` unchanged. That surfaces as `OSError`, then "no se pudo lanzar ssh" in `ejecutar`, or as `codigo None` in alta. `host_de_ssh` silently falls back to `destino.split("@")[-1]`, which is pre-existing and acceptable for a display-only host. Not a regression.

## Recommended actions
1. Replace the `shutil.which` use in `resuelve_programa` with an explicit, absolute-directory-only, `.exe`/`.com`-only lookup (fixes A1, A2, A3, A4). Update the docstring, which wrongly advertises `.cmd`/`.bat` as supported.
2. Gate the `.py` sidecar behind a test-only env var set in `tests/aislamiento.py`.
3. Add a regression test for the CWD `.cmd` case (A10).

## Unresolved questions
- Is there any entry point other than the tray (CLI, macOS launchd agent) that reaches `servidores_alta`/`servidores_ssh` without `chdir`? This decides how real A1 is outside the tray.

**Status:** DONE_WITH_CONCERNS
**Summary:** The original fix holds for its scenario. On Windows, `shutil.which` adds the CWD and `.cmd`/`.bat` back in, and the wheel/cmd.exe re-parsing bug was reproduced with the real `DIAGNOSTICO`. Medium severity, not blocking. One fix covers A1 to A4.
