# Adversarial review cycle 2 — resuelve_programa() rewrite (uncommitted diff)

Scope: `git diff HEAD -- infobar/plataforma.py tests/test_portabilidad.py tests/test_servidores_alta.py`
Env: Python 3.14.2, Windows 11. Tests: ResuelveProgramaTest 5/5 pass, test_servidores_alta 41/41 pass.

## Verdict per original finding

| ID | Status | Notes |
|---|---|---|
| A1 cwd lookup | **Mostly closed — 1 residual (R1)** | Resolution loop never touches cwd. But the not-found fallback returns the bare name, and CreateProcess then searches cwd itself. |
| A2 .cmd/.bat | **Closed** | Windows only tries `nombre+".exe"`/`".com"`. Bare-name fallback goes to CreateProcess, which only appends `.exe` and never runs `.cmd` from a bare name. |
| A3 empty/relative PATH | **Closed** | `not d or not isabs(d)` skips `""`, `"."`, `"foo"`, `"C:foo"` (verified: `ntpath.isabs("C:foo") == False`). |

## R1 (Low-Medium) — bare-name fallback reintroduces the cwd lookup via CreateProcess

`return orden` when nothing matches → `subprocess` → `CreateProcess(None, "ssh ...")`. CreateProcess search
order is: the python.exe dir, **then cwd**, then System32, Windows dir, PATH. Reproduced empirically: with
`NoDefaultCurrentDirectoryInExePath` unset, a `zzprog.exe` that existed only in cwd **was launched** through
`resuelve_programa(["zzprog"])` → fallback → `subprocess.run`.

Precondition: ssh / ssh-keygen / ssh-keyscan is not in any absolute PATH entry (OpenSSH Client optional
feature not installed, or PATH stripped). That's the same "user doesn't have ssh" path the app already has to
handle, so it's realistic on a fresh machine. Impact: a planted `ssh.exe` in the launch directory runs instead
of a clean "ssh not found" error.

Fix (small): on Windows, don't fall back to the bare name. Raise instead. All 3 callers already catch `OSError`
(`servidores_alta._ejecuta`, `servidores_ssh` Popen, `servidores_externo.host_de_ssh` catches `Exception`):
```python
    if os.name == "nt":
        raise FileNotFoundError(f"{nombre}: no esta en ningun directorio absoluto del PATH")
    return orden
```
Then update `test_nunca_resuelve_a_cmd_o_bat` / the not-found assertions to `assertRaises(FileNotFoundError)`.
(On POSIX, execvp never searches cwd unless PATH contains an empty/"." entry, so the fallback is a Windows-only issue.)

## R2 (Low, test quality) — the cwd regression test is vacuous on machines/harnesses that set NoDefaultCurrentDirectoryInExePath

This shell (Claude Code harness) has `NoDefaultCurrentDirectoryInExePath=1`. Verified: under it, the OLD
`shutil.which("programa")` returns the PATH copy, not the cwd one — so
`test_el_directorio_de_trabajo_actual_no_se_consulta` would pass against the old buggy code here. With the var
unset, old code returns `.\programa.EXE` (test would fail, as intended).
Fix: in `ResuelveProgramaTest.setUp`, `os.environ.pop("NoDefaultCurrentDirectoryInExePath", None)` with a
restoring `addCleanup`. Same for any R1 regression test (that one needs the var unset to observe CreateProcess's cwd step).

## Other checks (no issue)

- **COSTGUARD_FAKES_DIR**: an attacker who can set the process env can already set PATH (or PYTHONPATH,
  ComSpec, etc.), so this is not a materially new surface. Only nit: a *relative* value is joined against cwd;
  harmless since only the test sets it (absolute `self.bin`). Optional hardening: also require `os.path.isabs(fakes)`.
- **nombre with `/`, `\`, `..`, or absolute**: all callers pass literal constants (`ssh`, `ssh-keygen`,
  `ssh-keyscan`); not attacker-reachable.
- **Duplicate PATH dirs, case-folding, 8.3 aliases, symlinked PATH dirs**: all require write access to an
  absolute PATH directory, i.e. outside the threat model (same as prior "Reject" verdict). Duplicates just
  return the first hit; `WHOAMI` resolves case-insensitively as expected.
- **Python < 3.13**: `ntpath.isabs("\\foo")` returned True (rooted, drive-relative → current drive, not cwd
  dir). Requires attacker-controlled PATH; negligible.
- **Quoted PATH entries** (`"C:\Program Files\OpenSSH"`) are now skipped (not isabs). Minor functional
  regression vs CreateProcess; shutil.which had the same limitation. Non-blocking.
- **Docstring**: the shutil.which/3.12 paragraph explains *why it's not used*; accurate, not misleading.
  But "nunca en el directorio de trabajo actual" overstates the guarantee while R1 exists — fixing R1 makes it true.
- **Tests 2-5** test the real function with real files (no mocks of the logic under test); valid.

## Verdict

A2 and A3 fully resolved. A1 resolved inside the resolver but **residual R1** via the bare-name fallback
(conditional on ssh being absent from PATH). Not a hard blocker (narrow precondition), but it's a 3-line fix
and the docstring promises the opposite, so recommend fixing before commit, together with R2 (test env hygiene).

## Unresolved questions
- Project's minimum supported Python on Windows (affects only the negligible `\foo` isabs note).
