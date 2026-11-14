#!/usr/bin/env python3
"""The paper's figure of LLM-KG-Bench prompt lengths, from prompt_tokens.csv.

    python experiments/llm-kg-bench/plot_prompt_tokens.py [--tokenizer o200k_base]

Writes prompt_tokens_<tokeniser>.pdf and .png next to this script: one panel per task, one bar
per format, and for Loreto its change against Turtle. Needs matplotlib.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import PathPatch  # noqa: E402
from matplotlib.path import Path as MplPath  # noqa: E402

HERE = Path(__file__).resolve().parent

TASKS = ["Text2AnswerListOrganizational", "Sparql2AnswerListOrganizational",
         "RdfConnectionExplainStatic", "RdfFriendCount"]
TASK_SHORT = {"Text2AnswerListOrganizational": "Text2Answer", "Sparql2AnswerListOrganizational": "Sparql2Answer",
              "RdfConnectionExplainStatic": "ConnectionExplain", "RdfFriendCount": "FriendCount"}
FORMATS = ["loreto", "turtle", "json-ld", "xml", "ntriples"]
FORMAT_LABEL = {"loreto": "Loreto", "turtle": "Turtle", "json-ld": "JSON-LD", "xml": "RDF/XML", "ntriples": "N-Triples"}

COLUMN_IN = 3.33         # one column of ACM sigconf, in inches
COLOR = {"loreto": "#2a78d6", "turtle": "#eb6834", "json-ld": "#1baf7a", "xml": "#eda100", "ntriples": "#e87ba4"}
INK, INK_2, MUTED, GRID, BASELINE, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#ffffff"

plt.rcParams.update({
    "pdf.fonttype": 42, "ps.fonttype": 42,
    "font.family": "sans-serif",
    "font.sans-serif": ["Linux Biolinum O", "Liberation Sans", "Arial", "DejaVu Sans"],
    "font.size": 7, "axes.titlesize": 7.5, "axes.labelsize": 7, "xtick.labelsize": 6.5, "ytick.labelsize": 6.5,
    "text.color": INK, "axes.labelcolor": INK_2, "xtick.color": MUTED, "ytick.color": INK,
    "axes.edgecolor": BASELINE, "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0,
    "xtick.major.size": 2.5, "ytick.major.size": 0,
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
})


def rounded_bar(ax, y, width, height, color, radius_pt=1.6):
    """Horizontal bar from 0 with a rounded data end and a square baseline."""
    fig = ax.figure
    pos = ax.get_position()
    w_pt = pos.width * fig.get_figwidth() * 72
    h_pt = pos.height * fig.get_figheight() * 72
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    rx = min(radius_pt * (x1 - x0) / w_pt, width / 2)
    ry = min(radius_pt * abs(y1 - y0) / h_pt, height / 2)
    b, t = y - height / 2, y + height / 2
    verts = [(0, b), (width - rx, b), (width, b), (width, b + ry), (width, t - ry), (width, t), (width - rx, t),
             (0, t), (0, b)]
    codes = [MplPath.MOVETO, MplPath.LINETO, MplPath.CURVE3, MplPath.CURVE3, MplPath.LINETO, MplPath.CURVE3,
             MplPath.CURVE3, MplPath.LINETO, MplPath.CLOSEPOLY]
    ax.add_patch(PathPatch(MplPath(verts, codes), facecolor=color, edgecolor="none", zorder=2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tokenizer", default="o200k_base", choices=["o200k_base", "llama-3.1", "qwen3"])
    args = ap.parse_args()
    rows = list(csv.DictReader(open(HERE / "prompt_tokens.csv", encoding="utf-8")))
    panels = [(TASK_SHORT[t], {r["format"]: float(r[f"prompt_{args.tokenizer}"]) for r in rows if r["task"] == t})
              for t in TASKS]
    xmax = max(v for _t, vals in panels for v in vals.values()) * 1.3

    ncols, nrows = 2, 2
    axes_h, gap, top, bottom = 0.15 * len(FORMATS), 0.40, 0.17, 0.40
    height = top + nrows * axes_h + (nrows - 1) * gap + bottom
    fig, axes = plt.subplots(nrows, ncols, figsize=(COLUMN_IN, height), squeeze=False)
    fig.subplots_adjust(left=0.17, right=0.97, top=1 - top / height, bottom=bottom / height,
                        wspace=0.10, hspace=gap / axes_h)
    for idx, ax in enumerate(axes.flat):
        title, values = panels[idx]
        ax.set_xlim(0, xmax)
        ax.set_ylim(len(FORMATS) - 0.45, -0.55)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.grid(axis="x", color=GRID, linewidth=0.5)
        ax.set_axisbelow(True)
        ax.set_title(title, color=INK, pad=3, loc="left")
        for i, f in enumerate(FORMATS):
            v = values.get(f)
            if v is None:
                ax.text(xmax * 0.012, i, "not in this task", va="center", ha="left", color=MUTED, fontsize=6)
                continue
            rounded_bar(ax, i, v, 0.62, COLOR[f])
            label = f"{v:,.0f}"
            if f == "loreto" and values.get("turtle"):
                change = 100 * (v - values["turtle"]) / values["turtle"]
                label += f" ({change:+.1f}%)".replace("-", "−")
            ax.text(v + xmax * 0.015, i, label, va="center", ha="left", color=INK_2, fontsize=6)
        ax.set_yticks(range(len(FORMATS)))
        ax.set_yticklabels([FORMAT_LABEL[f] for f in FORMATS] if idx % ncols == 0 else [])
        ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(nbins=4, steps=[1, 2, 2.5, 5, 10]))
        ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _p: f"{x:,.0f}"))
    fig.supxlabel(f"Prompt tokens ({args.tokenizer})", y=0.04 / height, va="bottom", fontsize=7, color=INK_2)
    name = f"prompt_tokens_{args.tokenizer.split('_')[0]}"
    fig.savefig(HERE / f"{name}.pdf")
    fig.savefig(HERE / f"{name}.png", dpi=220)
    print(f"written {name}.pdf and {name}.png")


if __name__ == "__main__":
    main()
