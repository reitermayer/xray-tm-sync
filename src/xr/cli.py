"""xr: Xray Cloud from the command line, in the style of `uip`.

One codebase for people and agents: a script, a pipeline and a coding agent (through the xray, xray-tm-sync and
xray-tm-demo skills) all run these same commands and get the same output and command trail.

  xr login | logout | login status
  xr project list
  xr tests list | get | create | delete
  xr testsets list | get
  xr testexecutions list | get | import
  xr testplans list
  xr sync check | pull | plan | apply | testsets | report | status     (Xray → UiPath Test Manager)
  xr demo sandbox | seed | execute | report | reset                    (sandbox demos only)

Global options, anywhere on the line: --output json|table|plain, --output-filter <JMESPath on Data>.
Exit codes: 0 ok; 1 finished with failed items; 2 stopped before doing anything.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

from xr import __version__, sync
from xr.output import FORMATS, Output

TASK_GROUPS = ("sync", "demo")


def split_globals(argv: list[str]) -> tuple[list[str], str, str | None]:
    """Take --output / --output-filter out of the line, wherever they are (as uip accepts them)."""
    rest, fmt, expr = [], "json", None
    i = 0
    while i < len(argv):
        arg = argv[i]
        name, eq, value = arg.partition("=")
        if name in ("--output", "--output-filter"):
            if not eq:
                if i + 1 >= len(argv):
                    raise SystemExit(f"xr: {name} needs a value")
                value = argv[i + 1]
                i += 1
            if name == "--output":
                if value not in FORMATS:
                    raise SystemExit(f"xr: --output must be one of {', '.join(FORMATS)}")
                fmt = value
            else:
                expr = value
        else:
            rest.append(arg)
        i += 1
    return rest, fmt, expr


def task_code(group: str, argv: list[str]) -> str:
    """`sync plan …` → SyncPlan, `demo reset …` → DemoReset (the Code of the envelope)."""
    skip = False
    for arg in argv:
        if skip:
            skip = False
        elif arg == "--workdir":
            skip = True
        elif not arg.startswith("-"):
            return group.title() + arg.title()
    return group.title()


def run_task(group: str, argv: list[str], out: Output) -> int:
    code = task_code(group, argv)

    def envelope(data: Any) -> None:
        if isinstance(data, dict) and "error" in data:
            out.failure(code, data["error"], data=data.get("evidence") and {"evidence": data["evidence"]})
        else:
            out.success(code, data)

    sync.EMIT_HOOK = envelope
    try:
        if group == "sync":
            return sync.main(argv)
        from xr import demo

        return demo.main(argv)
    finally:
        sync.EMIT_HOOK = None


def main(argv: list[str] | None = None) -> int:
    from xr import resources

    raw = sys.argv[1:] if argv is None else list(argv)
    raw, fmt, expr = split_globals(raw)
    out = Output(fmt, expr)
    if raw and raw[0] in TASK_GROUPS:
        return run_task(raw[0], raw[1:], out)

    p = argparse.ArgumentParser(prog="xr", description=__doc__.split("\n\n")[0],
                                epilog="Task groups: xr sync --help, xr demo --help. Global: --output, --output-filter.")
    p.add_argument("--version", action="version", version=f"xr {__version__}")
    sub = p.add_subparsers(dest="group", metavar="{login,logout,project,tests,testsets,testexecutions,testplans,sync,demo}")
    handlers = resources.add_parsers(sub)
    for group in TASK_GROUPS:
        sub.add_parser(group, help={"sync": "Xray → UiPath Test Manager sync (xr sync --help)",
                                    "demo": "sandbox demo: seed, mock run, reset (xr demo --help)"}[group], add_help=False)
    args = p.parse_args(raw)
    if not args.group:
        p.print_help()
        return 2
    args.argv = raw
    code = args.group.title()
    try:
        if hasattr(args, "action"):
            resources.check_args(args)
        code, data = handlers[args.group](args)
        if isinstance(data, dict) and sync._TRAIL:  # writes leave a command trail
            data = data | {"Evidence": sync.trail_paths()}
        out.success(code, data)
        return 0
    except sync.IntakeError as exc:
        out.failure(code, str(exc))
        return 2
    except Exception as exc:
        if not type(exc).__module__.startswith(("httpx", "httpcore")):
            raise
        out.failure(code, f"network error talking to Xray: {type(exc).__name__}: {exc}", "Safe to re-run.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
