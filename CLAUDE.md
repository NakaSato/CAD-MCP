# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An MCP server (Python, FastMCP) that drives AutoCAD / GstarCAD (GCAD) / ZWCAD on **Windows** through COM (`pywin32`). It exposes 37 tools (draw, query, edit, layers, drawings, blocks, `send_command` passthrough) plus a `drawing://current` resource and a `cad-assistant` prompt. Code comments, log messages, and tool docstrings are in Chinese; keep that convention when editing existing code.

## Commands

Real runtime requires Windows with a CAD app installed — `cad_controller.py` raises at import time if `win32com`/`pythoncom` are missing. On macOS/Linux use the fake backend: `--fake` (or `CAD_MCP_FAKE=1`) swaps in `src/fake_cad.py`, an in-memory imitation of the AutoCAD COM object model, so the real controller code runs unchanged. `pywin32` is a Windows-only dependency marker, so `uv run` works on any OS.

```bash
pip install -r requirements.txt            # or: pip install -e .  (provides the `cad-mcp` entry point)
python src/server.py                        # stdio transport (default)
uv run python src/server.py --fake          # fake in-memory CAD backend (any OS)
python src/server.py --transport streamable-http --port 8000   # serves http://127.0.0.1:8000/mcp
python src/server.py --transport streamable-http --host 0.0.0.0 --allow-host <ip>   # remote clients (e.g. Mac -> Parallels VM)
npx -y @modelcontextprotocol/inspector python C:\\path\\to\\src\\server.py   # interactive tool testing
```

There is no test suite, linter, or CI configured.

## Architecture

Request flow: `server.py` tool function → `com(...)` → `COMExecutor` thread → `CADService` / `CADController` method → CAD COM object.

- **`src/server.py`** — FastMCP app. Defines constrained parameter types (`Point`, `Points`, `PosFloat`, `ArrayCount`, …) so bad input is rejected at the schema level, registers every tool with `@mcp.tool()` (read-only tools get `readOnlyHint`, destructive ones `destructiveHint`), and wraps results in Pydantic models from `models.py` so FastMCP emits `outputSchema`/`structuredContent`. Also contains `CADService`, a thin layer over the controller that auto-starts CAD and records a session log in `drawing_state` (used by the `drawing://current` resource). `main()` picks transport from CLI args, falling back to `config.json`.
- **`src/cad_controller.py`** — all COM interaction.
  - `COMExecutor` runs **every** COM call on one dedicated STA thread (`cad-com`). COM interface pointers must not cross threads: anything touching a COM object — including reading `entity.Handle` — must go through `await com(fn, ...)` in server.py (see `_entity_result`, `_extract_handles_sync`). Calling COM directly from the asyncio thread fails or crashes.
  - `_call_with_com_retry` retries transient errors (`RPC_E_CALL_REJECTED` when CAD is busy, AutoCAD transient save errors, and pywin32 `AttributeError`s that hide a rejected member lookup). Dead-connection HRESULTs (`_DEAD_COM_HRESULTS`) reset `self.app` so the next call reconnects instead of retrying.
  - `_get_app_info` maps `cad.type` to candidate ProgIDs (GCAD tries both `GCAD.Application` and `GstarCAD.Application`); `start_cad` attaches via `GetActiveObject` or launches via `Dispatch`.
  - Existing entities are addressed by CAD handle strings, resolved with `doc.HandleToObject`. `SendCommand` is asynchronous, so methods using it (undo/redo, passthrough) wait for completion before returning.
- **`src/gcad_mac.py`** — GstarCAD for Mac backend (`--gcad-mac` / `CAD_MCP_BACKEND=gcad-mac`). `GcadMacController` subclasses `CADController`; every method is one script round trip: write a `.scr` (AutoLISP), paste `_SCRIPT <path>` into GstarCAD's command line via System Events (paste + key codes only — typed letters follow the active input source), wait for the result file, parse it with `parse_sexpr`. Mac GstarCAD has no `vla-*`, `.scr` lines split on spaces, and an unknown command aborts the script, so values go inside `(command ...)`/`entmake`. Non-ASCII names must not be matched via `\\U+` escapes in LISP (`tblsearch` misses them) — see `_layer_expr`.
- **`src/fake_cad.py`** — fake backend. Exposes stand-ins for `win32com.client` / `pythoncom` plus fake Application/Document/ModelSpace/entity classes. Undo/redo snapshot the document state at the outermost `StartUndoMark`. When the controller starts calling a new COM member, add it here too or fake mode breaks.
- **`src/models.py`** — Pydantic result models (`ToolResult` with `entity_handles`, plus list/property result types). New tools should return one of these.
- **`src/config.py`** — `get_config()` is an `lru_cache` singleton over `src/config.json`, with built-in defaults if the file is missing or invalid. Import config only through this, never by re-reading the JSON.
- **`src/nlp_processor.py`** — legacy Chinese/English keyword parser behind the `process_command` tool; also provides `color_to_index`, used by every tool to accept ACI indexes or color names (e.g. `"红色"`, `"red"`).

## Gotchas

- **Dual import mode**: `server.py` and the other modules work both as a package (`src.server`, via the `cad-mcp` entry point) and as a script (`python src/server.py`, used by existing client configs). Keep the `if __package__: from .x import … else: from x import …` / `try/except ImportError` pattern when adding imports.
- **Logging**: `logging.basicConfig` must only be called in `server.py`. Calling it in `src/__init__.py` or elsewhere silently disables the rotating file handler (`cad_mcp.log` next to the source, or `~/.cad-mcp/` as a fallback). Logs go to stderr so they don't corrupt stdio protocol traffic — never `print()` to stdout.
- Network mode: FastMCP enables DNS-rebinding protection for localhost at construction, so binding to `0.0.0.0` alone still rejects remote Host headers (HTTP 421). `_configure_host_check` in `server.py` rebuilds the allow-list from `--host`/`--allow-host`.
- Tools that create entities call `_ensure_running(ctx)` first, which auto-starts CAD (up to `startup_wait_time`, ~20 s) and sends a progress message; editing tools instead return a failure result if CAD isn't running.
- When adding a tool, also update the tool lists in `PROMPT_TEMPLATE` (server.py) and the READMEs (`README.md`, `README_en.md`, `README_zh.md`, `README_th.md`).
