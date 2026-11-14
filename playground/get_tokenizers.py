#!/usr/bin/env python3
"""Download the Hugging Face tokenisers the Playground offers, and check their sha256.

    python get_tokenizers.py                  # into playground/tokenizers/
    python get_tokenizers.py --dest /opt/tokenizers

config/tokenizers.json lists, for every "hf-file" tokeniser, the Hugging Face repository, the
revision (a commit) and the sha256 of its tokenizer.json: the same files used for the corpus
measurements in experiments/. A file whose sha256 does not match is deleted and the script
fails. Files already present with the right sha256 are not downloaded again.

Each file is written to <dest>/<path> with a SOURCE.txt next to it (source, snapshot, sha256),
which the Playground shows as the tokeniser's provenance. The tiktoken encodings (o200k_base,
cl100k_base) are not handled here: tiktoken downloads and checks them itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
URL = "https://huggingface.co/{repo}/resolve/{revision}/tokenizer.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(entry: dict, dest: Path) -> None:
    target = dest / entry["path"]
    if target.is_file() and sha256(target) == entry["sha256"]:
        print(f"{entry['id']}: already present")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    url = URL.format(repo=entry["hf_repo"], revision=entry["revision"])
    print(f"{entry['id']}: {url}")
    with urllib.request.urlopen(url, timeout=120) as response, open(target, "wb") as fh:
        while block := response.read(1 << 20):
            fh.write(block)
    got = sha256(target)
    if got != entry["sha256"]:
        target.unlink()
        raise SystemExit(f"{entry['id']}: sha256 {got} does not match {entry['sha256']}")
    (target.parent / "SOURCE.txt").write_text(
        f"source: https://huggingface.co/{entry['hf_repo']}\nsnapshot: {entry['revision']}\nsha256: {got}\n",
        encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=HERE / "config" / "tokenizers.json")
    ap.add_argument("--dest", type=Path, default=HERE / "tokenizers")
    args = ap.parse_args()
    entries = [e for e in json.loads(args.config.read_text(encoding="utf-8")) if e["kind"] == "hf-file"]
    for entry in entries:
        fetch(entry, args.dest)
    print(f"{len(entries)} tokenisers in {args.dest}")


if __name__ == "__main__":
    sys.exit(main())
