#!/usr/bin/env python3
"""Token cost of standard RDF serialisations against Loreto and its ablations over a corpus.

    python experiments/corpus/benchmark_corpus.py --root path/to/ontologies --out results/ --verify \
        --tokenizers o200k_base,cl100k_base,hf:Qwen/Qwen3-8B

For every ontology, tokeniser and variant it records tokens, bytes and serialisation time
(parsing excluded), and with --verify it checks that every Loreto variant parses back to
the same RDF dataset. Loreto is optimised for each tokeniser it is measured with; the
"loreto-opt:<first tokeniser>" rows measure the text optimised for the first tokeniser
with the other ones.

Outputs in --out:
  per_ontology.csv       one row per ontology x tokeniser x variant
  summary_formats.csv    mean/median tokens, bytes and time per tokeniser x variant
  summary_reduction.csv  Loreto against every baseline format, per tokeniser
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # the repository root: loreto.py

from loreto import (  # noqa: E402
    LoretoOptions, TokenCounter, dataset_to_records, parse_to_dataset, records_to_union_graph, serialize, verify,
)

BASELINES = [   # (label, rdflib format, source)
    ("json-ld", "json-ld", "graph"),
    ("n3", "n3", "graph"),
    ("nquads", "nquads", "dataset"),
    ("ntriples", "nt", "graph"),
    ("rdfxml", "xml", "graph"),
    ("trig", "trig", "dataset"),
    ("turtle", "turtle", "graph"),
]

VARIANTS = {    # Loreto configurations: the full method and one switch off at a time
    "loreto": {},
    "loreto-flat": {"layout": "flat"},
    "loreto-grouped": {"layout": "grouped"},
    "loreto-no-prefixes": {"prefixes": False},
    "loreto-no-id-prefixes": {"id_prefixes": False},
    "loreto-short-aliases": {"aliases": "short"},
    "loreto-mnemonic-aliases": {"aliases": "mnemonic"},
    "loreto-no-dict": {"dictionary": False},
    "loreto-no-lists": {"lists": False},
    "loreto-no-inline": {"inline_bnodes": False},
    "loreto-no-literal-shorthand": {"literal_shorthand": False},
    "loreto-minimal": {"layout": "flat", "prefixes": False, "dictionary": False, "lists": False,
                       "inline_bnodes": False, "literal_shorthand": False},
    "loreto-legend": {"legend": True},
    "loreto-deskolemized": {"deskolemize": True},
}

FIELDS = ["ontology", "triples", "tokenizer", "variant", "kind", "tokens", "bytes", "serialise_ms",
          "layout", "verified", "error"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="directory with the ontologies")
    ap.add_argument("--glob", default="**/*.owl", help="files to read under --root (default: **/*.owl)")
    ap.add_argument("--tokenizers", default="o200k_base,cl100k_base",
                    help="comma-separated TokenCounter specs; the first one is the reference")
    ap.add_argument("--variants", default=",".join(VARIANTS), help="comma-separated Loreto variants")
    ap.add_argument("--out", default="benchmark_results", help="output directory")
    ap.add_argument("--verify", action="store_true", help="round-trip check of every Loreto variant (first tokenizer)")
    ap.add_argument("--limit", type=int, help="only the first N ontologies")
    args = ap.parse_args()

    files = sorted(p for p in Path(args.root).glob(args.glob) if p.is_file())[: args.limit]
    if not files:
        raise SystemExit(f"no files matching {args.glob!r} under {args.root}")
    tokenizers = [TokenCounter(spec) for spec in args.tokenizers.split(",") if spec]
    variants = [v for v in args.variants.split(",") if v]
    unknown = [v for v in variants if v not in VARIANTS]
    if unknown:
        raise SystemExit(f"unknown variants: {unknown}; choose from {list(VARIANTS)}")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for n, path in enumerate(files, 1):
        name = path.relative_to(args.root).as_posix()
        try:
            ds = parse_to_dataset(path)
        except Exception as e:  # noqa: BLE001
            print(f"[{n}/{len(files)}] {name}: parse error: {e}", file=sys.stderr)
            continue
        records = dataset_to_records(ds)
        union = records_to_union_graph(records)
        baselines = {"source": path.read_text(encoding="utf-8", errors="replace")}
        for label, fmt, source in BASELINES:
            try:
                text = (ds if source == "dataset" else union).serialize(format=fmt)
                baselines[label] = text.decode("utf-8") if isinstance(text, bytes) else text
            except Exception as e:  # noqa: BLE001
                rows.append({"ontology": name, "triples": len(records), "variant": label, "kind": "baseline",
                             "error": f"{type(e).__name__}: {e}"})

        reference_text = None
        for k, tok in enumerate(tokenizers):
            base = {"ontology": name, "triples": len(records), "tokenizer": tok.name}
            for label, text in baselines.items():
                rows.append({**base, "variant": label, "kind": "baseline", "tokens": tok.count(text),
                             "bytes": len(text.encode("utf-8"))})
            for variant in variants:
                opts = LoretoOptions(tokenizer=tok.name, **VARIANTS[variant])
                row = {**base, "variant": variant, "kind": "loreto"}
                try:
                    text, st = serialize(ds, opts, return_stats=True, counter=tok)
                    row.update(tokens=st.tokens["chosen"], bytes=st.bytes, serialise_ms=st.timings_ms["total"],
                               layout=st.layout)
                    if args.verify and k == 0:
                        row["verified"] = verify(ds, text, opts)
                    if variant == "loreto" and k == 0:
                        reference_text = text
                except Exception as e:  # noqa: BLE001
                    row["error"] = f"{type(e).__name__}: {e}"
                rows.append(row)
            if k > 0 and reference_text is not None:
                rows.append({**base, "variant": f"loreto-opt:{tokenizers[0].name}", "kind": "loreto",
                             "tokens": tok.count(reference_text), "bytes": len(reference_text.encode("utf-8"))})
        print(f"[{n}/{len(files)}] {name}: {len(records)} triples", file=sys.stderr)

    with open(out_dir / "per_ontology.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    # --- summaries
    groups = defaultdict(list)
    for r in rows:
        if r.get("tokens") is not None and r.get("tokenizer"):
            groups[(r["tokenizer"], r["variant"])].append(r)
    summary = []
    for (tk, variant), rs in sorted(groups.items()):
        times = [r["serialise_ms"] for r in rs if r.get("serialise_ms") is not None]
        checked = [r["verified"] for r in rs if r.get("verified") is not None]
        summary.append({
            "tokenizer": tk, "variant": variant, "kind": rs[0]["kind"], "n": len(rs),
            "mean_tokens": round(statistics.mean(r["tokens"] for r in rs), 2),
            "median_tokens": statistics.median(r["tokens"] for r in rs),
            "mean_bytes": round(statistics.mean(r["bytes"] for r in rs), 2),
            "mean_serialise_ms": round(statistics.mean(times), 3) if times else "",
            "max_serialise_ms": round(max(times), 3) if times else "",
            "verified": f"{sum(checked)}/{len(checked)}" if checked else "",
        })
    with open(out_dir / "summary_formats.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0]))
        w.writeheader()
        w.writerows(summary)

    by_key = {(r["ontology"], r["tokenizer"], r["variant"]): r["tokens"]
              for r in rows if r.get("tokens") is not None and r.get("tokenizer")}
    reduction = []
    for tok in tokenizers:
        for label in ["source"] + [b[0] for b in BASELINES]:
            pairs = [(by_key[(o, t, label)], by_key[(o, t, "loreto")])
                     for (o, t, v) in by_key if t == tok.name and v == label and (o, t, "loreto") in by_key]
            if not pairs:
                continue
            mean_base = statistics.mean(b for b, _ in pairs)
            mean_loreto = statistics.mean(lo for _, lo in pairs)
            reduction.append({
                "tokenizer": tok.name, "baseline": label, "n": len(pairs),
                "mean_baseline_tokens": round(mean_base, 2), "mean_loreto_tokens": round(mean_loreto, 2),
                "reduction_of_means_pct": round(100 * (mean_base - mean_loreto) / mean_base, 2),
                "median_reduction_pct": round(statistics.median(100 * (b - lo) / b for b, lo in pairs if b), 2),
                "loreto_smaller": f"{sum(lo < b for b, lo in pairs)}/{len(pairs)}",
            })
    with open(out_dir / "summary_reduction.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(reduction[0]))
        w.writeheader()
        w.writerows(reduction)

    errors = [r for r in rows if r.get("error")]
    failed = [r for r in rows if r.get("verified") is False]
    print(f"\n{len(files)} files, {len(rows)} rows -> {out_dir}/")
    for r in reduction:
        print(f"  {r['tokenizer']:>14} {r['baseline']:>8}: {r['mean_baseline_tokens']:>10} -> "
              f"{r['mean_loreto_tokens']:>9} tokens  ({r['reduction_of_means_pct']:6.2f}%, "
              f"median {r['median_reduction_pct']:6.2f}%, smaller in {r['loreto_smaller']})")
    print(f"errors: {len(errors)}   round-trip failures: {len(failed)}")
    for r in (errors + failed)[:10]:
        print("  ", r["ontology"], r.get("tokenizer"), r["variant"], r.get("error") or "verify=False")
    return 1 if failed or any(r["kind"] == "loreto" for r in errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
