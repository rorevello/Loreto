#!/usr/bin/env python3
"""Screenshots of the running Playground (material for the paper's figure and the video).

    python experiments/figures/screenshots.py [--url http://localhost:8000] [--out experiments/figures/screenshots]

Needs Playwright for Python (pip install playwright) and a Chromium build. Playwright cannot
install its browsers on Ubuntu 20.04, so by default this uses the headless shell already in
~/.cache/ms-playwright; set CHROME to use another executable.

Writes: top-light.png, top-dark.png (first screen), full-light.png (whole page), panes.png
(the side-by-side card), matrix.png (tokenisers), ido-full.png (the IDO example), mobile.png and
paper-teaser.png and paper-teaser-ido.png (summary and token bars for the paper, twice the pixel density).
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import os
from pathlib import Path

from playwright.async_api import async_playwright

READY = ("document.querySelector('#matrix table') && !document.querySelector('#matrix .placeholder') "
         "&& !document.querySelector('#ablation .placeholder') && document.getElementById('busy').hidden")


def chrome() -> str:
    if os.environ.get("CHROME"):
        return os.environ["CHROME"]
    found = sorted(glob.glob(os.path.expanduser(
        "~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell")))
    if not found:
        raise SystemExit("No Chromium found: set CHROME to a Chromium or Chrome executable.")
    return found[-1]


async def main(url: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=chrome())
        for scheme in ("light", "dark"):
            page = await browser.new_page(viewport={"width": 1600, "height": 1000}, color_scheme=scheme)
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(url)
            await page.wait_for_function(READY, timeout=60000)
            await page.screenshot(path=out / f"top-{scheme}.png")
            if scheme == "light":
                await page.screenshot(path=out / "full-light.png", full_page=True)
                await page.locator("section.card").nth(1).screenshot(path=out / "panes.png")
                await page.locator("section.card").nth(3).screenshot(path=out / "matrix.png")
                await page.select_option("#example-select", "ido")
                await page.wait_for_function("document.getElementById('kpi-triples').textContent === '1,399'",
                                             timeout=60000)
                await page.wait_for_function(READY, timeout=120000)
                await page.screenshot(path=out / "ido-full.png", full_page=True)
            print(f"{scheme}: page errors {errors or 'none'}")
            await page.close()
        # Paper figure: the summary and the token bars at twice the pixel density, so the text
        # stays legible at the width of a two-column ACM figure. Once with the paper's example
        # and once with IDO (a whole BioPortal ontology).
        page = await browser.new_page(viewport={"width": 1600, "height": 590}, device_scale_factor=2)
        await page.goto(url)
        await page.wait_for_function(READY, timeout=60000)
        await page.screenshot(path=out / "paper-teaser.png")
        await page.select_option("#example-select", "ido")
        await page.wait_for_function("document.getElementById('kpi-triples').textContent === '1,399'",
                                     timeout=60000)
        await page.wait_for_function(READY, timeout=120000)
        await page.screenshot(path=out / "paper-teaser-ido.png")
        await page.close()
        mobile = await browser.new_page(viewport={"width": 390, "height": 844})
        await mobile.goto(url)
        await mobile.wait_for_function(READY, timeout=60000)
        await mobile.screenshot(path=out / "mobile.png", full_page=True)
        width = await mobile.evaluate("document.documentElement.scrollWidth")
        print(f"mobile: page width {width}px (390 means no horizontal scroll)")
        await browser.close()
    print(f"written to {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8000/")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "screenshots"))
    args = ap.parse_args()
    asyncio.run(main(args.url, Path(args.out)))
