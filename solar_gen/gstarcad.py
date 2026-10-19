"""Drive the GstarCAD for Mac window from Python.

GstarCAD on macOS has no COM/AppleScript API and ignores its /b command-line switch when
already running, but it does run CAD scripts (.scr, which may contain AutoLISP lines).
We type "_SCRIPT", paste the script path (Cmd+V, clipboard restored afterwards) and press Return
via macOS Accessibility (System Events) - setting the field's AXValue does not reach CAD's input buffer; the script writes a done-file when it finishes. The terminal app
needs Accessibility permission, and nobody should type into GstarCAD during the few seconds a
job takes (keys typed meanwhile land in its command line).

Script rules learnt the hard way:
- a space or blank line in a .scr line acts as Enter, so anything with spaces goes in LISP;
- an unknown command aborts the script, so keep every value inside (command ...) / (setvar ...);
- VLA functions (vla-*) do not exist on Mac - use entget/entmod and command-line commands.
"""
from __future__ import annotations

import subprocess
import tempfile
import time
import uuid
from pathlib import Path

WORK = Path(tempfile.gettempdir()) / "solar_gen_gcad"

_TYPE_SCRIPT = r'''
on run argv
  set saved to missing value
  try
    set saved to the clipboard
  end try
  tell application "System Events"
    set p to first process whose unix id is (item 1 of argv as integer)
    set frontmost of p to true
    delay 0.4
    repeat 4 times
      key code 53
      delay 0.1
    end repeat
    set cmd to text field 1 of window 1 of p
    -- no typed letters (they follow the active input source, e.g. Thai); paste + key codes only
    set the clipboard to "_SCRIPT"
    try
      set focused of cmd to true
    end try
    delay 0.15
    key code 9 using command down
    delay 0.15
    key code 36
    delay 0.5
    set the clipboard to (item 2 of argv)
    try
      set focused of cmd to true
    end try
    delay 0.15
    key code 9 using command down
    delay 0.15
    key code 36
  end tell
  delay 0.3
  if saved is not missing value then
    try
      set the clipboard to saved
    end try
  end if
end run
'''


class GstarCADError(RuntimeError):
    pass


def lisp_str(s: str) -> str:
    """Quote a Python string as an AutoLISP string literal (ASCII only).

    Non-ASCII characters become CAD unicode escapes (\\U+0E2A), which GstarCAD stores as the real
    characters - this sidesteps the unknown encoding of .scr/.lsp files.
    """
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\n":
            out.append("\\n")
        elif ord(ch) < 128:
            out.append(ch)
        else:
            out.append(f"\\\\U+{ord(ch):04X}")
    return '"' + "".join(out) + '"'


def _pid() -> int:
    r = subprocess.run(["pgrep", "-n", "gcad"], capture_output=True, text=True)
    if r.returncode != 0:
        raise GstarCADError("GstarCAD is not running - open it (with a blank drawing) and try again")
    return int(r.stdout.split()[0])


def safe_path(p: Path) -> str:
    """Script file prompts split on spaces, so paths typed into scripts must not contain any."""
    s = str(p)
    if " " in s or not s.isascii():
        raise GstarCADError(f"path must be ASCII without spaces for GstarCAD scripts: {s}")
    return s


def run(lines: list[str], timeout: float = 120.0, label: str = "job") -> str:
    """Run script lines in the open GstarCAD; returns what the script wrote to its done-file."""
    WORK.mkdir(parents=True, exist_ok=True)
    tag = f"{label}-{uuid.uuid4().hex[:8]}"
    done, scr = WORK / f"{tag}.done", WORK / f"{tag}.scr"
    finish = (f'(progn (setq __f (open {lisp_str(str(done))} "w")) '
              f'(write-line (if (boundp (quote __result)) (vl-princ-to-string __result) "ok") __f) '
              f'(close __f) (princ))')
    scr.write_text("\n".join(["FILEDIA 0", "(setq __result nil)", *lines, finish, ""]), encoding="ascii")
    subprocess.run(["osascript", "-", str(_pid()), safe_path(scr)], input=_TYPE_SCRIPT, text=True,
                   capture_output=True, check=True)
    t0 = time.time()
    while time.time() - t0 < timeout:
        if done.exists():
            time.sleep(0.2)
            return done.read_text(encoding="utf-8", errors="replace").strip()
        time.sleep(0.5)
    raise GstarCADError(f"GstarCAD did not finish '{label}' within {timeout:.0f}s (check for an open dialog)")


def open_drawing(path: Path) -> list[str]:
    return ["_.OPEN", safe_path(path)]


def save_as(path: Path, version: str = "2018") -> list[str]:
    return [f'(command "_.SAVEAS" "{version}" {lisp_str(str(path))})']


def plot_all_layouts(paper: str = "ISO full bleed A3 (420.00 x 297.00 MM)", device: str = "DWG To PDF.pc3") -> list[str]:
    """Plot every layout to PDF next to the drawing (default file names <dwg>-<layout>.pdf)."""
    return [f'(foreach __l (layoutlist) (command "_.-PLOT" "_Y" __l {lisp_str(device)} {lisp_str(paper)}) '
            f'(while (> (getvar "CMDACTIVE") 0) (command "")))']


def lisp_set_text(handle: str, text: str) -> str:
    """LISP line that replaces the text (group 1) of the entity with this handle."""
    return (f'(if (setq __e (handent "{handle}")) (progn (setq __d (entget __e)) '
            f'(entmod (subst (cons 1 {lisp_str(text)}) (assoc 1 __d) __d)) (entupd __e)))')
