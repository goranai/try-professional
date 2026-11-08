"""Draw demo.json as an animated SVG terminal. Free, and no new dependencies.

usage: python render_demo.py <demo.json> <out.svg>

Every line it shows is real output from record_demo.py. It shortens long output with a marked "…", replaces waiting with
a line saying how long it was, and types each command at a steady pace. The README says all three.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

WIDTH, COLUMNS, ROWS = 980, 112, 26
LINE, TOP, LEFT = 21, 46, 18
FONT = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"
COLORS = {"bg": "#16181d", "bar": "#22252c", "text": "#e6e6e6", "prompt": "#7ee787", "dim": "#8b949e", "cmd": "#ffffff"}
TYPE_SECONDS = 1.2
LINE_SECONDS = 0.06
# Long enough to read: a few seconds, and more for a full screen.
HOLD_SECONDS = 3.0
HOLD_PER_LINE = 0.18
# A shortened screen keeps its ending, because that's where the result is.
TAIL = 7


def _lines(text: str, keep: int) -> list[str]:
    """Wrapped, without blank runs, and cut to `keep` lines with a marked ellipsis."""
    out: list[str] = []
    for raw in text.replace("\r", "").splitlines():
        raw = raw.replace("**", "").replace("`", "")
        if not raw.strip():
            if out and out[-1] != "":
                out.append("")
            continue
        out.extend(textwrap.wrap(raw, COLUMNS, subsequent_indent="  ", replace_whitespace=False) or [""])
    if len(out) > keep:
        out = [*out[: keep - TAIL - 1], "… (shortened)", *out[-TAIL:]]
    return out


def _waited(seconds: float) -> str:
    minutes, rest = divmod(round(seconds), 60)
    return f"# {minutes} min {rest} s of waiting, cut" if minutes else f"# {rest} s of waiting, cut"


def scenes(demo: dict[str, Any]) -> list[list[tuple[str, str, float]]]:
    """Each scene is one screen: (kind, text, seconds after the scene starts)."""
    out = []
    for step in demo["steps"]:
        prompt = "> " if step.get("in_claude_code") else "$ "
        typed = textwrap.wrap(step["typed"], COLUMNS - 2) or [""]
        rows: list[tuple[str, str, float]] = [("cmd", prompt + typed[0], 0.0)]
        rows += [("cont", "  " + more, TYPE_SECONDS) for more in typed[1:]]
        at = TYPE_SECONDS
        if step["seconds"] >= 5:
            rows.append(("dim", _waited(step["seconds"]), at))
            at += 0.8
        for line in _lines(step["output"], ROWS - len(rows)):
            rows.append(("text", line, at))
            at += LINE_SECONDS
        out.append(rows)
    return out


def _visible(begin: float, end: float, total: float) -> str:
    """A discrete opacity animation: shown from begin to end, every loop."""
    b, e = max(begin / total, 0.0001), min(end / total, 1)
    return (
        f'<animate attributeName="opacity" values="0;1;0" keyTimes="0;{b:.5f};{e:.5f}" calcMode="discrete" '
        f'dur="{total:.2f}s" repeatCount="indefinite"/>'
    )


def render(demo: dict[str, Any]) -> str:
    screens = scenes(demo)
    durations = [rows[-1][2] + HOLD_SECONDS + HOLD_PER_LINE * len(rows) for rows in screens]
    total = sum(durations)
    height = TOP + ROWS * LINE + 16
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" '
        f'role="img" aria-label="A real run of Try Professional in Claude Code: setup, design, approval, a command, '
        f'and its deliverables">',
        f'<rect width="{WIDTH}" height="{height}" rx="10" fill="{COLORS["bg"]}"/>',
        f'<rect width="{WIDTH}" height="30" rx="10" fill="{COLORS["bar"]}"/>',
        f'<rect y="20" width="{WIDTH}" height="10" fill="{COLORS["bar"]}"/>',
        *(
            f'<circle cx="{20 + 18 * i}" cy="15" r="5.5" fill="{c}"/>'
            for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840"))
        ),
        f'<text x="{WIDTH / 2}" y="19" fill="{COLORS["dim"]}" font-family="{FONT}" font-size="12" '
        f'text-anchor="middle">Try Professional: a real run, with waiting cut</text>',
        f'<g font-family="{FONT}" font-size="13.5" xml:space="preserve">',
    ]
    start = 0.0
    for rows, duration in zip(screens, durations, strict=True):
        end = start + duration
        for number, (kind, text, at) in enumerate(rows):
            y = TOP + number * LINE
            color = COLORS["cmd"] if kind in ("cmd", "cont") else COLORS["dim"] if kind == "dim" else COLORS["text"]
            if kind == "cmd":
                prompt, typed = text[:2], text[2:]
                # Typed a few characters at a time, then the whole line stays.
                chunks = max(1, min(12, len(typed) // 6))
                for chunk in range(1, chunks + 1):
                    shown = typed[: round(len(typed) * chunk / chunks)]
                    begin = start + TYPE_SECONDS * (chunk - 1) / chunks
                    stop = start + TYPE_SECONDS * chunk / chunks if chunk < chunks else end
                    parts.append(
                        f'<text x="{LEFT}" y="{y}" opacity="0"><tspan fill="{COLORS["prompt"]}">{escape(prompt)}</tspan>'
                        f'<tspan fill="{color}" font-weight="600">{escape(shown)}</tspan>{_visible(begin, stop, total)}</text>'
                    )
                continue
            weight = ' font-weight="600"' if kind == "cont" else ""
            parts.append(
                f'<text x="{LEFT}" y="{y}" fill="{color}"{weight} opacity="0">{escape(text)}'
                f"{_visible(start + at, end, total)}</text>"
            )
        start = end
    parts += ["</g>", "</svg>"]
    return "\n".join(parts)


def main(source: Path, out: Path) -> None:
    demo = json.loads(source.read_text(encoding="utf-8"))
    svg = render(demo)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8", newline="\n")
    print(f"{out}: {len(svg.encode('utf-8')) / 1024:.0f} KB, {len(demo['steps'])} screens")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
