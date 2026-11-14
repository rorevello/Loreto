#!/usr/bin/env python3
"""Table 1 of the paper: Loreto against the standard formats over the BioPortal corpus, with five
tokenisers, including the stronger "Turtle, all prefixes" baseline.

    python experiments/corpus/turtle_all_prefixes.py --root path/to/ontologies

Run it from the repository root, with the Playground requirements installed and the Hugging Face
tokenisers downloaded (python playground/get_tokenizers.py).

- "Turtle (rdflib)" is the union graph written by rdflib, the baseline of benchmark_corpus.py.
  rdflib only invents prefixes for predicates, so subjects and objects are written as full IRIs.
- "Turtle, all prefixes" declares a prefix for every namespace used twice or more and drops the
  declarations the body never uses. It uses the same functions as the Playground
  (playground/app/service.py: prefixed_graph and drop_unused_prefixes).

The Loreto, JSON-LD, RDF/XML and N-Triples tokens are read from results/o200k_cl100k/per_ontology.csv
(o200k_base, cl100k_base) and results/hf_tokenizers/per_ontology.csv (Qwen3, DeepSeek-V3,
Llama 3.1), written by benchmark_corpus.py. Loreto is the full method optimised for each
tokeniser. The rdflib Turtle is recomputed here and checked against those files. MEDO, whose
invalid IRI rdflib cannot write as Turtle or N-Triples, is left out, which leaves 87 ontologies.

Outputs in results/turtle_all_prefixes/:
  per_ontology.csv  one row per ontology and tokeniser
  table1.csv        mean tokens per format and Loreto's saving, per tokeniser (Table 1)
  summary.txt       the same, with the number of ontologies where Loreto is shorter
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RESULTS = HERE / "results"

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "playground"))

import loreto  # noqa: E402

from app.service import drop_unused_prefixes, prefixed_graph  # noqa: E402

# label: (TokenCounter spec, results folder, tokeniser name in that folder's CSV)
def tokenizers(hf_dir: Path) -> dict:
    return {
        "o200k_base": ("o200k_base", "o200k_cl100k", "o200k_base"),
        "cl100k_base": ("cl100k_base", "o200k_cl100k", "cl100k_base"),
        "Qwen3": (f"hf-file:{hf_dir / 'qwen3' / 'tokenizer.json'}", "hf_tokenizers", "hf:Qwen/Qwen3-8B"),
        "DeepSeek-V3": (f"hf-file:{hf_dir / 'deepseek-v3' / 'tokenizer.json'}", "hf_tokenizers",
                        "hf:deepseek-ai/DeepSeek-V3"),
        "Llama 3.1": (f"hf-file:{hf_dir / 'llama-3.1' / 'tokenizer.json'}", "hf_tokenizers",
                      "hf:NousResearch/Hermes-3-Llama-3.1-8B"),
    }


MEASURED = {"loreto": "loreto", "turtle": "turtle_rdflib_in_csv", "json-ld": "jsonld",
            "rdfxml": "rdfxml", "ntriples": "ntriples"}
FORMATS = [("loreto", "Loreto"), ("turtle_all_prefixes", "Turtle (all prefixes)"),
           ("turtle_rdflib", "Turtle (rdflib)"), ("rdfxml", "RDF/XML"), ("jsonld", "JSON-LD"),
           ("ntriples", "N-Triples")]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, required=True, help="folder with the ontologies (see manifest.csv)")
    ap.add_argument("--glob", default="**/*.owl")
    ap.add_argument("--tokenizers-dir", type=Path, default=REPO / "playground" / "tokenizers")
    args = ap.parse_args()
    toks = tokenizers(args.tokenizers_dir)
    out = RESULTS / "turtle_all_prefixes"
    out.mkdir(parents=True, exist_ok=True)

    measured = {}
    for label, (_, folder, csv_name) in toks.items():
        for r in csv.DictReader(open(RESULTS / folder / "per_ontology.csv", encoding="utf-8")):
            if r["tokenizer"] == csv_name and r["variant"] in MEASURED and r["tokens"]:
                measured.setdefault((label, r["ontology"]), {})[MEASURED[r["variant"]]] = int(r["tokens"])
    counters = {label: loreto.TokenCounter(spec) for label, (spec, _, _) in toks.items()}

    rows = []
    for path in sorted(p for p in args.root.glob(args.glob) if p.is_file()):
        name = path.relative_to(args.root).as_posix()
        if not all(len(measured.get((label, name), {})) == len(MEASURED) for label in toks):
            continue        # MEDO: rdflib cannot write it as Turtle or N-Triples
        ds = loreto.parse_to_dataset(path)
        records = loreto.dataset_to_records(ds)
        declared = {str(ns): p for p, ns in ds.namespaces() if p and not p.startswith(("ns", "default"))}
        turtle_rdflib = loreto.records_to_union_graph(records).serialize(format="turtle")
        turtle_prefixed = drop_unused_prefixes(
            prefixed_graph(records, declared, loreto.guess_namespace).serialize(format="turtle"))
        for label, counter in counters.items():
            rows.append({"ontology": name, "tokenizer": label, "triples": len(records),
                         "turtle_rdflib": counter.count(turtle_rdflib),
                         "turtle_all_prefixes": counter.count(turtle_prefixed),
                         **measured[(label, name)]})
    if not rows:
        raise SystemExit(f"no ontology of {args.root} matches results/o200k_cl100k/per_ontology.csv")

    with open(out / "per_ontology.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    mean = statistics.mean
    table, lines = [], []
    for key, title in FORMATS:
        row = {"format": title}
        for label in toks:
            rs = [r for r in rows if r["tokenizer"] == label]
            lo, fm = mean(r["loreto"] for r in rs), mean(r[key] for r in rs)
            row[f"{label} mean tokens"] = round(fm)
            row[f"{label} saved by Loreto (%)"] = "" if key == "loreto" else round(100 * (1 - lo / fm), 1)
        table.append(row)
    with open(out / "table1.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(table[0]))
        w.writeheader()
        w.writerows(table)

    for label in toks:
        rs = [r for r in rows if r["tokenizer"] == label]
        lo = mean(r["loreto"] for r in rs)
        tp = [r["turtle_all_prefixes"] for r in rs]
        lines += [
            f"== {label}: {len(rs)} ontologies; rdflib Turtle equal to the results CSV: "
            f"{sum(r['turtle_rdflib'] == r['turtle_rdflib_in_csv'] for r in rs)}",
            "mean tokens: " + ", ".join(f"{title} {mean(r[key] for r in rs):.0f}" for key, title in FORMATS),
            "reduction of the means: " + ", ".join(
                f"vs {title} {100 * (1 - lo / mean(r[key] for r in rs)):.2f}% "
                f"(Loreto shorter in {sum(r['loreto'] < r[key] for r in rs)}/{len(rs)})"
                for key, title in FORMATS[1:]),
            "smallest reductions vs Turtle (all prefixes): " + ", ".join(
                f"{r['ontology'].split('/')[0]} {100 * (1 - r['loreto'] / r['turtle_all_prefixes']):.1f}%"
                for r in sorted(rs, key=lambda r: 1 - r["loreto"] / r["turtle_all_prefixes"])[:3]),
        ]
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
