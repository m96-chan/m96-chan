#!/usr/bin/env python3
"""Generate a 3D isometric contribution graph SVG for GitHub profile.

Uses only Python stdlib. Fetches contribution data via GitHub GraphQL API.
Falls back to dummy data when GITHUB_TOKEN is not set (for local preview).
Includes ANSI art background with eye blink animation.
"""

import json
import math
import os
import re
import random
import urllib.request
import urllib.error
from datetime import date, timedelta
from pathlib import Path

# --- Configuration ---
USERNAME = os.environ.get("GITHUB_USERNAME", "m96-chan")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
OUTPUT = os.environ.get("OUTPUT_FILE", "contribution-3d.svg")
SCRIPT_DIR = Path(__file__).parent
MOTD_ART = os.environ.get("MOTD_ART", str(SCRIPT_DIR.parent / ".motd_art"))

# Isometric projection parameters
ISO_X_SCALE = 14.0
ISO_Y_SCALE = 7.5
BASE_HEIGHT = 5
MAX_EXTRA = 8
SCALE_FACTOR = 0.5

# SVG canvas
SVG_WIDTH = 880
SVG_HEIGHT = 390
ORIGIN_X = 115
ORIGIN_Y = 350

# Color scheme (GitHub Dark / Chartreuse-Dark)
COLORS = {
    0: {"top": "#161b22", "front": "#11151a", "right": "#0d1117"},
    1: {"top": "#0e4429", "front": "#0a3520", "right": "#072a19"},
    2: {"top": "#006d32", "front": "#005828", "right": "#004620"},
    3: {"top": "#26a641", "front": "#1e8535", "right": "#186b2b"},
    4: {"top": "#39d353", "front": "#2eaa43", "right": "#258836"},
}

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DAY_LABELS = {1: "Mon", 3: "Wed", 5: "Fri"}


def iso(x: float, y: float, z: float) -> tuple[float, float]:
    """Convert 3D coordinates to 2D isometric projection (30-degree)."""
    cos30 = math.cos(math.radians(30))
    sin30 = 0.5
    sx = ORIGIN_X + (x - y) * cos30 * ISO_X_SCALE
    sy = ORIGIN_Y - (x + y) * sin30 * ISO_Y_SCALE - z
    return (round(sx, 2), round(sy, 2))


# --- ANSI Art Parser ---

ANSI_RE = re.compile(rb'\x1b\[([^m]*)m')


def parse_ansi_art(filepath: str) -> list[list[dict]]:
    """Parse ANSI art file into a grid of cells with color info.

    Each cell: {"char": str, "fg": (r,g,b)|None, "bg": (r,g,b)|None}
    """
    with open(filepath, "rb") as f:
        raw = f.read()

    grid = []
    for line in raw.split(b"\n"):
        cells = []
        fg = None
        bg = None
        reverse = False
        pos = 0

        while pos < len(line):
            m = ANSI_RE.match(line, pos)
            if m:
                codes = m.group(1).decode("ascii", errors="replace").split(";")
                i = 0
                while i < len(codes):
                    c = codes[i]
                    if c in ("0", ""):
                        fg = bg = None
                        reverse = False
                    elif c == "7":
                        reverse = True
                    elif c == "?25l" or c == "?25h":
                        pass  # cursor visibility, ignore
                    elif c == "38" and i + 4 < len(codes) and codes[i + 1] == "2":
                        fg = (int(codes[i + 2]), int(codes[i + 3]), int(codes[i + 4]))
                        i += 4
                    elif c == "48" and i + 4 < len(codes) and codes[i + 1] == "2":
                        bg = (int(codes[i + 2]), int(codes[i + 3]), int(codes[i + 4]))
                        i += 4
                    i += 1
                pos = m.end()
            else:
                byte = line[pos]
                if byte < 0x80:
                    ch = chr(byte)
                    pos += 1
                elif byte < 0xC0:
                    pos += 1
                    continue
                elif byte < 0xE0:
                    ch = line[pos : pos + 2].decode("utf-8", errors="replace")
                    pos += 2
                elif byte < 0xF0:
                    ch = line[pos : pos + 3].decode("utf-8", errors="replace")
                    pos += 3
                else:
                    ch = line[pos : pos + 4].decode("utf-8", errors="replace")
                    pos += 4

                if ch in ("$", "\r"):
                    continue

                actual_fg = bg if reverse else fg
                actual_bg = fg if reverse else bg
                cells.append({"char": ch, "fg": actual_fg, "bg": actual_bg})

        grid.append(cells)

    # Trim trailing empty/cursor-control lines
    while grid and (not grid[-1] or all(c["char"].strip() == "" for c in grid[-1])):
        grid.pop()

    return grid


def is_purple(color: tuple | None) -> bool:
    """Check if a color is purple/magenta (high R and B relative to G)."""
    if not color:
        return False
    r, g, b = color
    return r > g * 1.3 and b > g * 1.3 and max(r, b) > 70


def rgb_str(color: tuple) -> str:
    return f"rgb({color[0]},{color[1]},{color[2]})"


def render_ansi_art_svg(grid: list[list[dict]]) -> tuple[str, str, str, list[dict], str]:
    """Render ANSI art with neon glow overlays.

    Returns (art_svg, outline_svg, purple_glow_svg, edge_anchors, char_mask_d).
    - art: full character rendered as colored rects+text (dimmed)
    - outline: purple silhouette edge (top + sides only, no bottom)
    - purple_glow: clothing/hair purple parts
    - edge_anchors: list of {"x", "y", "side"} for circuit trace origins
    - char_mask_d: SVG path d-attribute for character silhouette (pixel-level)
    """
    if not grid:
        return "", "", "", [], ""

    num_rows = len(grid)
    num_cols = max(len(row) for row in grid) if grid else 80

    # Maintain 4:3 aspect ratio (terminal chars are ~1:2 width:height)
    cell_h = SVG_HEIGHT / num_rows
    cell_w = cell_h / 2
    art_width = cell_w * num_cols
    offset_x = (SVG_WIDTH - art_width) / 2
    offset_y = 0

    # Build filled grid for edge detection
    filled = []
    for row in grid:
        frow = []
        for cell in row:
            has_content = cell["char"].strip() != "" or cell["bg"] is not None
            is_dark = (
                (cell["fg"] is None or max(cell["fg"]) < 25)
                and (cell["bg"] is None or max(cell["bg"]) < 25)
            )
            frow.append(has_content and not is_dark)
        frow.extend([False] * (num_cols - len(frow)))
        filled.append(frow)

    # Detect edge cells — top and sides only (exclude bottom edges)
    def is_top_or_side_edge(r: int, c: int) -> bool:
        if not filled[r][c]:
            return False
        # Check top, left, right neighbors (NOT bottom)
        for dr, dc in [(-1, 0), (0, -1), (0, 1)]:
            nr, nc = r + dr, c + dc
            if nr < 0 or nc < 0 or nc >= num_cols:
                return True
            if not filled[nr][nc]:
                return True
        return False

    art_parts = []
    outline_rects = []
    purple_rects = []
    edge_cells = []  # collect (x, y, side) for circuit trace anchors

    art_parts.append(f'<g class="motd-art" opacity="0.25">')

    for ri, row in enumerate(grid):
        for ci, cell in enumerate(row):
            if ci >= num_cols:
                break
            x = offset_x + ci * cell_w
            y = offset_y + ri * cell_h
            ch = cell["char"]
            fg_color = cell["fg"]
            bg_color = cell["bg"]

            # --- Full art layer (all cells) ---
            if bg_color and bg_color != (0, 0, 0):
                art_parts.append(
                    f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell_w + 0.5:.1f}" '
                    f'height="{cell_h + 0.5:.1f}" fill="{rgb_str(bg_color)}"/>'
                )
            if ch.strip() and fg_color:
                tx = x + cell_w * 0.5
                ty = y + cell_h * 0.82
                esc = ch.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                art_parts.append(
                    f'<text x="{tx:.1f}" y="{ty:.1f}" fill="{rgb_str(fg_color)}" '
                    f'font-family="monospace" font-size="{cell_h:.1f}px" '
                    f'text-anchor="middle">{esc}</text>'
                )

            # --- Glow overlay layers ---
            cell_purple = is_purple(cell["fg"]) or is_purple(cell["bg"])

            if cell_purple:
                purple_rects.append(
                    f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell_w:.1f}" '
                    f'height="{cell_h:.1f}" fill="#a855f7" rx="0.5"/>'
                )

            if is_top_or_side_edge(ri, ci):
                outline_rects.append(
                    f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell_w:.1f}" '
                    f'height="{cell_h:.1f}" fill="#7c3aed" rx="0.5"/>'
                )
                # Determine which side this edge is on
                cx = x + cell_w / 2
                cy = y + cell_h / 2
                if ci > 0 and (ci >= num_cols - 1 or not filled[ri][ci + 1]):
                    edge_cells.append({"x": cx, "y": cy, "side": "right"})
                elif ci == 0 or not filled[ri][ci - 1]:
                    edge_cells.append({"x": cx, "y": cy, "side": "left"})
                elif ri == 0 or not filled[ri - 1][ci]:
                    edge_cells.append({"x": cx, "y": cy, "side": "top"})

    art_parts.append("</g>")

    # Build character silhouette path from filled grid (row-run merging)
    # Each contiguous run of filled cells in a row → one rect hole
    char_holes = []
    for ri, frow in enumerate(filled):
        run_start = None
        for ci in range(len(frow)):
            if frow[ci]:
                if run_start is None:
                    run_start = ci
            else:
                if run_start is not None:
                    x1 = offset_x + run_start * cell_w
                    y1 = offset_y + ri * cell_h
                    x2 = offset_x + ci * cell_w
                    y2 = y1 + cell_h
                    char_holes.append(
                        f"M {x1:.1f} {y1:.1f} L {x2:.1f} {y1:.1f} "
                        f"L {x2:.1f} {y2:.1f} L {x1:.1f} {y2:.1f} Z"
                    )
                    run_start = None
        # Close run at end of row
        if run_start is not None:
            x1 = offset_x + run_start * cell_w
            y1 = offset_y + ri * cell_h
            x2 = offset_x + len(frow) * cell_w
            y2 = y1 + cell_h
            char_holes.append(
                f"M {x1:.1f} {y1:.1f} L {x2:.1f} {y1:.1f} "
                f"L {x2:.1f} {y2:.1f} L {x1:.1f} {y2:.1f} Z"
            )
    char_mask_d = " ".join(char_holes)

    art_svg = "\n".join(art_parts)

    outline_svg = (
        '<g class="art-outline">\n'
        + "\n".join(outline_rects)
        + "\n</g>"
    ) if outline_rects else ""

    purple_svg = (
        '<g class="art-purple">\n'
        + "\n".join(purple_rects)
        + "\n</g>"
    ) if purple_rects else ""

    return art_svg, outline_svg, purple_svg, edge_cells, char_mask_d


# --- Contribution Data ---

def fetch_contributions() -> list[dict]:
    """Fetch contribution data from GitHub GraphQL API."""
    query = """
    query($username: String!) {
      user(login: $username) {
        contributionsCollection {
          contributionCalendar {
            totalContributions
            weeks {
              contributionDays {
                contributionCount
                contributionLevel
                date
              }
            }
          }
        }
      }
    }
    """
    payload = json.dumps({
        "query": query,
        "variables": {"username": USERNAME}
    }).encode()

    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "contribution-3d-generator",
        },
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())

    calendar = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    total = calendar["totalContributions"]
    weeks = calendar["weeks"]

    days = []
    for wi, week in enumerate(weeks):
        for di, day in enumerate(week["contributionDays"]):
            level_map = {
                "NONE": 0,
                "FIRST_QUARTILE": 1,
                "SECOND_QUARTILE": 2,
                "THIRD_QUARTILE": 3,
                "FOURTH_QUARTILE": 4,
            }
            days.append({
                "week": wi,
                "day": di,
                "count": day["contributionCount"],
                "level": level_map.get(day["contributionLevel"], 0),
                "date": day["date"],
            })
    return days, total


def generate_dummy_data() -> list[dict]:
    """Generate dummy contribution data for local preview."""
    random.seed(42)
    today = date.today()
    start = today - timedelta(days=364)
    days = []
    total = 0

    github_dow = (start.weekday() + 1) % 7
    wi = 0
    di = github_dow

    for i in range(365):
        d = start + timedelta(days=i)
        dow = d.weekday()
        if dow < 5:
            r = random.random()
            if r < 0.15:
                level, count = 0, 0
            elif r < 0.35:
                level, count = 1, random.randint(1, 3)
            elif r < 0.6:
                level, count = 2, random.randint(3, 7)
            elif r < 0.85:
                level, count = 3, random.randint(7, 15)
            else:
                level, count = 4, random.randint(15, 30)
        else:
            r = random.random()
            if r < 0.4:
                level, count = 0, 0
            elif r < 0.65:
                level, count = 1, random.randint(1, 2)
            elif r < 0.85:
                level, count = 2, random.randint(2, 5)
            else:
                level, count = 3, random.randint(5, 10)

        total += count
        days.append({
            "week": wi, "day": di, "count": count,
            "level": level, "date": d.isoformat(),
        })
        di += 1
        if di >= 7:
            di = 0
            wi += 1

    return days, total


# --- Language Data ---

LANG_COLORS = {
    "Python": "#3572A5", "TypeScript": "#3178c6", "JavaScript": "#f1e05a",
    "C#": "#178600", "C": "#555555", "C++": "#f34b7d", "Rust": "#dea584",
    "HLSL": "#aace60", "ShaderLab": "#222c37", "Shell": "#89e051",
    "HTML": "#e34c26", "CSS": "#563d7c", "Go": "#00ADD8", "Java": "#b07219",
    "Ruby": "#701516", "Jupyter Notebook": "#DA5B0B", "Makefile": "#427819",
}


def fetch_languages() -> list[dict]:
    """Fetch language stats from GitHub GraphQL API."""
    query = """
    query($username: String!) {
      user(login: $username) {
        repositories(first: 100, ownerAffiliations: OWNER,
                     orderBy: {field: PUSHED_AT, direction: DESC}) {
          nodes {
            languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
              edges {
                size
                node { name color }
              }
            }
          }
        }
      }
    }
    """
    payload = json.dumps({"query": query, "variables": {"username": USERNAME}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "contribution-3d-generator",
        },
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())

    totals: dict[str, dict] = {}
    for repo in data["data"]["user"]["repositories"]["nodes"]:
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            color = edge["node"]["color"] or "#ccc"
            size = edge["size"]
            if name in totals:
                totals[name]["size"] += size
            else:
                totals[name] = {"name": name, "color": color, "size": size}

    ranked = sorted(totals.values(), key=lambda x: x["size"], reverse=True)
    total_size = sum(l["size"] for l in ranked)
    for lang in ranked:
        lang["pct"] = lang["size"] / total_size * 100 if total_size else 0
    return ranked


def dummy_languages() -> list[dict]:
    """Dummy language data for local preview."""
    langs = [
        ("Python", 38), ("TypeScript", 22), ("C#", 14),
        ("C++", 10), ("JavaScript", 7), ("HLSL", 5), ("Shell", 4),
    ]
    return [
        {"name": n, "color": LANG_COLORS.get(n, "#ccc"), "pct": p}
        for n, p in langs
    ]


def render_lang_chart(languages: list[dict]) -> str:
    """Render an animated donut chart for top languages."""
    # Chart params — bottom-right corner
    cx, cy = 810, 255
    r = 42           # radius
    stroke_w = 12    # donut thickness
    circumference = 2 * math.pi * r

    top = languages[:6]  # max 6 segments
    # Collapse remainder into "Other"
    shown_pct = sum(l["pct"] for l in top)
    if shown_pct < 100:
        top.append({"name": "Other", "color": "#484f58", "pct": 100 - shown_pct})

    parts = []
    parts.append(f'<g class="lang-chart">')

    # Dark background circle
    parts.append(
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
        f'stroke="#161b22" stroke-width="{stroke_w + 2}"/>'
    )

    offset = 0  # cumulative dashoffset
    for i, lang in enumerate(top):
        arc_len = (lang["pct"] / 100) * circumference
        if arc_len < 0.5:
            continue
        delay = 0.8 + i * 0.15  # staggered draw-in
        # Each segment: a circle with stroke-dasharray
        parts.append(
            f'<circle class="lang-seg" cx="{cx}" cy="{cy}" r="{r}" fill="none" '
            f'stroke="{lang["color"]}" stroke-width="{stroke_w}" '
            f'stroke-dasharray="{arc_len:.2f} {circumference - arc_len:.2f}" '
            f'stroke-dashoffset="{-offset:.2f}" '
            f'style="animation-delay: {delay:.2f}s;">'
            f'<title>{lang["name"]} ({lang["pct"]:.1f}%)</title>'
            f'</circle>'
        )
        offset += arc_len

    # Center label
    parts.append(
        f'<text x="{cx}" y="{cy - 3}" text-anchor="middle" '
        f'fill="#c9d1d9" font-family="-apple-system, BlinkMacSystemFont, sans-serif" '
        f'font-size="10" font-weight="600">Languages</text>'
    )
    parts.append(
        f'<text x="{cx}" y="{cy + 10}" text-anchor="middle" '
        f'fill="#8b949e" font-family="-apple-system, BlinkMacSystemFont, sans-serif" '
        f'font-size="8">{len(languages)} used</text>'
    )

    # Legend below chart
    legend_y = cy + r + 22
    for i, lang in enumerate(top[:5]):
        lx = cx - 44
        ly = legend_y + i * 13
        parts.append(
            f'<rect x="{lx}" y="{ly - 5}" width="6" height="6" rx="1" fill="{lang["color"]}"/>'
        )
        parts.append(
            f'<text x="{lx + 10}" y="{ly}" fill="#8b949e" '
            f'font-family="-apple-system, BlinkMacSystemFont, sans-serif" '
            f'font-size="8">{lang["name"]} {lang["pct"]:.0f}%</text>'
        )

    parts.append("</g>")
    return "\n".join(parts)


# --- Circuit Trace Rendering ---

def _bezier_approx_len(p0, p1, p2, p3, steps=20):
    """Approximate cubic bezier length by sampling."""
    length = 0.0
    prev = p0
    for i in range(1, steps + 1):
        t = i / steps
        u = 1 - t
        x = u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0]
        y = u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]
        dx, dy = x - prev[0], y - prev[1]
        length += math.sqrt(dx * dx + dy * dy)
        prev = (x, y)
    return length


def render_circuit_traces(edge_anchors: list[dict], count: int = 24,
                          seed: int = 42) -> str:
    """Render GPU-style traces with Knight Rider comet effect.

    A bright dot travels along each path with a long fading trail behind it,
    like a comet. Nothing stays lit — the trail moves with the dot.
    """
    if not edge_anchors:
        return ""

    rng = random.Random(seed)

    left = sorted([a for a in edge_anchors if a["side"] == "left"], key=lambda a: a["y"])
    right = sorted([a for a in edge_anchors if a["side"] == "right"], key=lambda a: a["y"])
    top = sorted([a for a in edge_anchors if a["side"] == "top"], key=lambda a: a["x"])

    def pick_spaced(anchors, n):
        if len(anchors) <= n:
            return anchors
        step = len(anchors) / n
        return [anchors[int(i * step)] for i in range(n)]

    # Distribute count across sides proportionally
    n_left = max(1, round(count * 0.4))
    n_right = max(1, round(count * 0.4))
    n_top = max(1, count - n_left - n_right)
    chosen = []
    chosen += [(a, "left") for a in pick_spaced(left, n_left)]
    chosen += [(a, "right") for a in pick_spaced(right, n_right)]
    chosen += [(a, "top") for a in pick_spaced(top, n_top)]
    rng.shuffle(chosen)
    chosen = chosen[:count]

    parts = []
    parts.append('<g class="circuit-traces">')

    for i, (anchor, side) in enumerate(chosen):
        ax, ay = anchor["x"], anchor["y"]
        tid = f"tr{i}"

        if side == "left":
            ex, ey = -5, max(10, min(SVG_HEIGHT - 10, ay + rng.uniform(-80, 80)))
            c1x = ax - (ax - ex) * 0.3 + rng.uniform(-20, 20)
            c1y = ay + rng.uniform(-30, 30)
            c2x = ex + (ax - ex) * 0.15
            c2y = ey + rng.uniform(-20, 20)
        elif side == "right":
            ex, ey = SVG_WIDTH + 5, max(10, min(SVG_HEIGHT - 10, ay + rng.uniform(-80, 80)))
            c1x = ax + (ex - ax) * 0.3 + rng.uniform(-20, 20)
            c1y = ay + rng.uniform(-30, 30)
            c2x = ex - (ex - ax) * 0.15
            c2y = ey + rng.uniform(-20, 20)
        else:
            ex = max(10, min(SVG_WIDTH - 10, ax + rng.uniform(-100, 100)))
            ey = -5
            c1x = ax + rng.uniform(-30, 30)
            c1y = ay - ay * 0.3
            c2x = ex + rng.uniform(-20, 20)
            c2y = ey + ay * 0.15

        path_d = (
            f"M {ax:.1f} {ay:.1f} "
            f"C {c1x:.1f} {c1y:.1f}, {c2x:.1f} {c2y:.1f}, {ex:.1f} {ey:.1f}"
        )
        plen = _bezier_approx_len((ax, ay), (c1x, c1y), (c2x, c2y), (ex, ey))
        plen = max(plen, 1)

        width = rng.uniform(0.8, 1.5)
        # Travel time + pause before next loop
        travel = 3.0 + plen / 120
        dur = f"{travel * 1.4:.1f}s"  # 40% pause at end
        begin = f"{i * 0.9:.1f}s"
        # Comet travels 0%→70% of dur, pauses 70%→100%
        t_end = 0.70

        # Trail lengths (percentage of path) — layered for gradient fade
        trail_outer = min(plen * 0.35, 150)  # long dim outer glow
        trail_mid = min(plen * 0.20, 90)     # medium
        trail_core = min(plen * 0.08, 35)    # bright short core
        # Gap = total path len so only one dash visible at a time
        gap = plen + trail_outer

        # offset: start at +gap (hidden), end at -plen (fully passed)
        off_start = f"{gap:.1f}"
        off_end = f"{-plen:.1f}"

        # Hidden reference path
        parts.append(f'<path id="{tid}" d="{path_d}" fill="none" stroke="none"/>')

        # Base wire — visible circuit trace from outline to edge
        parts.append(
            f'<path d="{path_d}" fill="none" '
            f'stroke="#4c1d95" stroke-width="{width + 0.3:.1f}" stroke-linecap="round" '
            f'opacity="0.55"/>'
        )

        # Layer 1: outer glow (widest trail, dimmest)
        parts.append(
            f'<path d="{path_d}" fill="none" '
            f'stroke="#7c3aed" stroke-width="{width + 2:.1f}" stroke-linecap="round" '
            f'stroke-dasharray="{trail_outer:.1f} {gap:.1f}" '
            f'stroke-dashoffset="{off_start}" opacity="0.25">'
            f'<animate attributeName="stroke-dashoffset" '
            f'values="{off_start};{off_end};{off_start}" '
            f'keyTimes="0;{t_end};1" calcMode="spline" '
            f'keySplines="0.4 0 0.6 1;0 0 1 1" '
            f'dur="{dur}" begin="{begin}" repeatCount="indefinite"/>'
            f'</path>'
        )

        # Layer 2: mid trail
        parts.append(
            f'<path d="{path_d}" fill="none" '
            f'stroke="#a855f7" stroke-width="{width + 0.8:.1f}" stroke-linecap="round" '
            f'stroke-dasharray="{trail_mid:.1f} {gap:.1f}" '
            f'stroke-dashoffset="{off_start}" opacity="0.5">'
            f'<animate attributeName="stroke-dashoffset" '
            f'values="{off_start};{off_end};{off_start}" '
            f'keyTimes="0;{t_end};1" calcMode="spline" '
            f'keySplines="0.4 0 0.6 1;0 0 1 1" '
            f'dur="{dur}" begin="{begin}" repeatCount="indefinite"/>'
            f'</path>'
        )

        # Layer 3: bright core trail
        parts.append(
            f'<path d="{path_d}" fill="none" '
            f'stroke="#c084fc" stroke-width="{width * 0.5:.1f}" stroke-linecap="round" '
            f'stroke-dasharray="{trail_core:.1f} {gap:.1f}" '
            f'stroke-dashoffset="{off_start}" opacity="0.8">'
            f'<animate attributeName="stroke-dashoffset" '
            f'values="{off_start};{off_end};{off_start}" '
            f'keyTimes="0;{t_end};1" calcMode="spline" '
            f'keySplines="0.4 0 0.6 1;0 0 1 1" '
            f'dur="{dur}" begin="{begin}" repeatCount="indefinite"/>'
            f'</path>'
        )

        # Layer 4: bright dot head
        parts.append(
            f'<circle r="2.5" fill="#e9d5ff" opacity="0">'
            f'<animateMotion dur="{dur}" begin="{begin}" repeatCount="indefinite" '
            f'keyPoints="0;1;1" keyTimes="0;{t_end};1" calcMode="spline" '
            f'keySplines="0.4 0 0.6 1;0 0 1 1">'
            f'<mpath href="#{tid}"/>'
            f'</animateMotion>'
            f'<animate attributeName="opacity" '
            f'values="0;1;1;0;0" '
            f'keyTimes="0;0.02;{t_end - 0.02};{t_end};1" '
            f'dur="{dur}" begin="{begin}" repeatCount="indefinite"/>'
            f'</circle>'
        )

    parts.append("</g>")
    return "\n".join(parts)


# --- Cyber Particle Rendering ---

PARTICLE_COLORS = ["#39d353", "#26a641", "#00ff41", "#0ff", "#2eaa43"]

# Half-width katakana, digits, symbols — matrix rain aesthetic
GARBAGE_CHARS = (
    "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ"
    "0123456789"
    "{}[]|/:;=+*#@$%!?"
)


def render_particles(count: int = 40, seed: int = 7) -> str:
    """Render falling garbage-character rain with depth (near/far layers)."""
    rng = random.Random(seed)
    parts = []
    parts.append('<g class="particles">')

    for i in range(count):
        # depth: 0.0 (far/small) → 1.0 (near/large)
        depth = rng.random()

        x = rng.uniform(10, SVG_WIDTH - 10)
        font_size = 5 + depth * 14          # 5–19px
        color = rng.choice(PARTICLE_COLORS)
        opacity = 0.1 + depth * 0.6         # 0.1–0.7
        duration = 14 - depth * 9           # 14s(far/slow) → 5s(near/fast)
        delay = rng.uniform(0, 12)
        drift = rng.uniform(-10, 10) * (1 - depth * 0.5)
        ch = rng.choice(GARBAGE_CHARS)

        parts.append(
            f'<text class="particle" x="{x:.1f}" y="-10" '
            f'fill="{color}" opacity="{opacity:.2f}" '
            f'font-family="monospace" font-size="{font_size:.1f}px" '
            f'text-anchor="middle" '
            f'style="animation-duration:{duration:.1f}s;'
            f'animation-delay:{delay:.1f}s;'
            f'--drift:{drift:.1f}px;">{ch}</text>'
        )

    parts.append("</g>")
    return "\n".join(parts)


# --- 3D Bar Rendering ---

def bar_height(level: int, count: int) -> float:
    if level == 0:
        return 1
    return BASE_HEIGHT + level * 2.5 + min(count * SCALE_FACTOR, MAX_EXTRA)


def make_bar_polygon(week: int, day: int, height: float) -> dict:
    x, y = week, day
    t0 = iso(x, y, height)
    t1 = iso(x + 1, y, height)
    t2 = iso(x + 1, y + 1, height)
    t3 = iso(x, y + 1, height)
    b1 = iso(x + 1, y, 0)
    b2 = iso(x + 1, y + 1, 0)
    b3 = iso(x, y + 1, 0)
    return {
        "top": [t0, t1, t2, t3],
        "front": [t3, t2, b2, b3],
        "right": [t2, t1, b1, b2],
    }


def points_str(pts: list[tuple]) -> str:
    return " ".join(f"{p[0]},{p[1]}" for p in pts)


# --- SVG Generation ---

def generate_svg(days: list[dict], total: int,
                 art_base: str = "", art_outline: str = "",
                 art_purple: str = "", lang_chart: str = "",
                 circuit_svg: str = "", char_mask_d: str = "") -> str:
    """Generate the complete SVG string."""
    bars = []
    for d in days:
        h = bar_height(d["level"], d["count"])
        poly = make_bar_polygon(d["week"], d["day"], h)
        bars.append({**d, "height": h, "poly": poly})

    bars.sort(key=lambda b: -(b["week"] + b["day"]))

    month_labels = []
    seen_months = set()
    for d in days:
        month = d["date"][:7]
        if month not in seen_months:
            seen_months.add(month)
            month_idx = int(d["date"][5:7]) - 1
            month_labels.append({"name": MONTH_NAMES[month_idx], "week": d["week"]})

    svg_parts = []

    # Build clip-path that excludes exact character pixels (even-odd holes)
    if char_mask_d:
        clip_def = (
            f'<clipPath id="clip-outside-char">'
            f'<path d="M 0 0 L {SVG_WIDTH} 0 L {SVG_WIDTH} {SVG_HEIGHT} L 0 {SVG_HEIGHT} Z '
            f'{char_mask_d}" clip-rule="evenodd"/>'
            f'</clipPath>'
        )
    else:
        clip_def = ""

    # SVG header + filters + styles
    svg_parts.append(f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SVG_WIDTH} {SVG_HEIGHT}" width="{SVG_WIDTH}" height="{SVG_HEIGHT}">
<defs>
  <!-- Neon glow filters -->
  <filter id="glow-strong" x="-100%" y="-100%" width="300%" height="300%">
    <feGaussianBlur in="SourceGraphic" stdDeviation="6" result="blur"/>
    <feMerge>
      <feMergeNode in="blur"/>
      <feMergeNode in="SourceGraphic"/>
    </feMerge>
  </filter>
  <filter id="glow-purple" x="-50%" y="-50%" width="200%" height="200%">
    <feGaussianBlur in="SourceGraphic" stdDeviation="4" result="blur"/>
    <feMerge>
      <feMergeNode in="blur"/>
      <feMergeNode in="blur"/>
      <feMergeNode in="SourceGraphic"/>
    </feMerge>
  </filter>
  {clip_def}

  <style>
    .bg {{ fill: #0d1117; }}
    .art-outline {{ filter: url(#glow-strong); animation: glow-breathe 5s ease-in-out infinite; }}
    .art-purple {{ filter: url(#glow-purple); animation: glow-breathe 5s ease-in-out infinite; }}
    @keyframes glow-breathe {{
      0%, 100% {{ opacity: 0.2; }}
      50% {{ opacity: 0.55; }}
    }}
    .circuit-traces {{ clip-path: url(#clip-outside-char); }}
    .bar-group {{
      opacity: 0;
      animation: rise 0.6s cubic-bezier(0.34, 1.56, 0.64, 1) forwards,
                 wave 4s ease-in-out infinite;
    }}
    @keyframes rise {{
      from {{ opacity: 0; transform: translateY(18px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    @keyframes wave {{
      0%, 100% {{ transform: translateY(0); opacity: 1; }}
      8%  {{ transform: translateY(-5px); opacity: 0.75; }}
      16% {{ transform: translateY(0); opacity: 1; }}
      22% {{ transform: translateY(-3px); opacity: 0.85; }}
      30% {{ transform: translateY(0); opacity: 1; }}
    }}
    .lang-seg {{
      transform: rotate(-90deg);
      transform-origin: 810px 255px;
      opacity: 0;
      animation: seg-draw 0.7s cubic-bezier(0.25, 0.46, 0.45, 0.94) forwards;
    }}
    @keyframes seg-draw {{
      from {{ stroke-dasharray: 0 9999; opacity: 0.5; }}
      to {{ opacity: 1; }}
    }}''')

    svg_parts.append('''
    .label {{ fill: #8b949e; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; font-size: 10px; }}
    .title {{ fill: #c9d1d9; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; font-size: 14px; font-weight: 600; }}
    .subtitle {{ fill: #8b949e; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; font-size: 11px; }}
  </style>
</defs>

<!-- Background -->
<rect class="bg" width="{w}" height="{h}" rx="6" />
'''.format(w=SVG_WIDTH, h=SVG_HEIGHT))

    # Art layers: base art → outline → purple glow → eyes (back to front)
    if art_base:
        svg_parts.append("<!-- Character art (dimmed) -->")
        svg_parts.append(art_base)
    if art_outline:
        svg_parts.append("<!-- Silhouette outline (top + sides) -->")
        svg_parts.append(art_outline)
    if art_purple:
        svg_parts.append("<!-- Purple glow (clothes/hair) -->")
        svg_parts.append(art_purple)

    # Circuit traces from character outline
    if circuit_svg:
        svg_parts.append("<!-- Circuit Traces -->")
        svg_parts.append(circuit_svg)

    # Title
    svg_parts.append(f'''
<!-- Title -->
<text class="title" x="20" y="28">{total:,} contributions in the last year</text>
<text class="subtitle" x="20" y="46">@{USERNAME}</text>
''')

    # Month labels
    for ml in month_labels:
        pos = iso(ml["week"], 0, 0)
        svg_parts.append(
            f'<text class="label" x="{pos[0]}" y="{pos[1] - 20}" '
            f'text-anchor="middle">{ml["name"]}</text>\n'
        )

    # Day labels
    for di, name in DAY_LABELS.items():
        pos = iso(0, di + 0.5, 0)
        svg_parts.append(
            f'<text class="label" x="{pos[0] - 20}" y="{pos[1] + 3}" '
            f'text-anchor="end">{name}</text>\n'
        )

    # Draw bars
    for bar in bars:
        level = bar["level"]
        colors = COLORS[level]
        poly = bar["poly"]
        delay = (bar["week"] + bar["day"]) * 0.015
        # Wave ripple: spread delays across 2s so wave visibly sweeps across
        wave_delay = (bar["week"] + bar["day"]) * 0.04 + 1.0
        count = bar["count"]
        dt = bar["date"]
        s = "s" if count != 1 else ""
        tooltip = f"{count} contribution{s} on {dt}"

        svg_parts.append(
            f'<g class="bar-group" style="animation-delay: {delay:.3f}s, {wave_delay:.3f}s;">'
            f"<title>{tooltip}</title>"
        )
        svg_parts.append(
            f'  <polygon class="right-{level}" points="{points_str(poly["right"])}" '
            f'fill="{colors["right"]}" stroke="#0d1117" stroke-width="0.5"/>'
        )
        svg_parts.append(
            f'  <polygon class="front-{level}" points="{points_str(poly["front"])}" '
            f'fill="{colors["front"]}" stroke="#0d1117" stroke-width="0.5"/>'
        )
        svg_parts.append(
            f'  <polygon class="top-{level}" points="{points_str(poly["top"])}" '
            f'fill="{colors["top"]}" stroke="#0d1117" stroke-width="0.5"/>'
        )
        svg_parts.append("</g>")

    # Language donut chart
    if lang_chart:
        svg_parts.append("<!-- Language Chart -->")
        svg_parts.append(lang_chart)

    svg_parts.append("</svg>")
    return "\n".join(svg_parts)


def main():
    # Load contribution data
    if TOKEN:
        print(f"Fetching contribution data for @{USERNAME}...")
        try:
            days, total = fetch_contributions()
            print(f"Fetched {total} contributions across {len(days)} days.")
        except (urllib.error.URLError, KeyError) as e:
            print(f"API error: {e}. Falling back to dummy data.")
            days, total = generate_dummy_data()
    else:
        print("No GITHUB_TOKEN set. Using dummy data for local preview.")
        days, total = generate_dummy_data()

    # Load and render ANSI art as layered glow effects
    art_base = art_outline = art_purple = ""
    edge_anchors = []
    char_mask_d = ""
    motd_path = MOTD_ART
    if os.path.exists(motd_path):
        print(f"Loading MOTD art from {motd_path}...")
        grid = parse_ansi_art(motd_path)
        print(f"Parsed {len(grid)} rows, max {max(len(r) for r in grid)} cols.")
        art_base, art_outline, art_purple, edge_anchors, char_mask_d = render_ansi_art_svg(grid)
        print(f"Art SVG: base={len(art_base)}B, outline={len(art_outline)}B, "
              f"purple={len(art_purple)}B, anchors={len(edge_anchors)}, "
              f"mask={len(char_mask_d)}B")
    else:
        print(f"MOTD art not found at {motd_path}, skipping background.")

    # Generate circuit traces from character outline
    circuit_svg = render_circuit_traces(edge_anchors) if edge_anchors else ""
    if circuit_svg:
        print(f"Circuit traces: {len(circuit_svg)}B")

    # Fetch language stats
    if TOKEN:
        try:
            languages = fetch_languages()
            print(f"Fetched {len(languages)} languages.")
        except (urllib.error.URLError, KeyError) as e:
            print(f"Language API error: {e}. Using dummy data.")
            languages = dummy_languages()
    else:
        languages = dummy_languages()
    lang_chart = render_lang_chart(languages)

    svg = generate_svg(days, total, art_base, art_outline, art_purple,
                       lang_chart, circuit_svg, char_mask_d)

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(svg)

    print(f"Generated {OUTPUT} ({len(svg):,} bytes)")


if __name__ == "__main__":
    main()
