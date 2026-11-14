"""What the API computes: the standard serialisations, Loreto with the chosen options, the
round-trip check, the ablation table and the comparison across tokenisers.

The standard serialisations are produced as in Loreto/scripts/benchmark_corpus.py, the script
behind the corpus results of the paper: graph formats from the union graph, TriG and N-Quads
from the dataset.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass
from types import ModuleType
from typing import Callable, Dict, List, Optional, Tuple, Union

from rdflib import Dataset, Graph, Literal, URIRef

from .config import Settings
from .rdf_input import INPUT_FORMATS, InputError, parse_text
from .tokenizers import Registry, RequestCounter, Tokenizer


@dataclass(frozen=True)
class Baseline:
    id: str
    label: str
    rdflib_format: str
    source: str      # "graph" (union of all graphs), "dataset", or "prefixed" (see prefixed_graph)
    ext: str


BASELINES = [
    Baseline("turtle-prefixed", "Turtle, all prefixes", "turtle", "prefixed", "ttl"),
    Baseline("turtle", "Turtle (rdflib)", "turtle", "graph", "ttl"),
    Baseline("trig", "TriG", "trig", "dataset", "trig"),
    Baseline("ntriples", "N-Triples", "nt", "graph", "nt"),
    Baseline("nquads", "N-Quads", "nquads", "dataset", "nq"),
    Baseline("jsonld", "JSON-LD", "json-ld", "graph", "jsonld"),
    Baseline("rdfxml", "RDF/XML", "xml", "graph", "rdf"),
]
_BASELINES = {b.id: b for b in BASELINES}
INPUT_ID = "input"
LORETO_ID = "loreto"

# Ablation: the full method against one change at a time (as in benchmark_corpus.py).
ABLATIONS: List[Tuple[str, str, str, dict]] = [
    ("full", "Full method", "", {}),
    ("layout-grouped", "Grouped layout only", "layout", {"layout": "grouped"}),
    ("layout-flat", "Flat layout only", "layout", {"layout": "flat"}),
    ("no-prefixes", "No namespace prefixes", "prefixes", {"prefixes": False}),
    ("no-id-prefixes", "No OBO identifier prefixes", "prefixes", {"id_prefixes": False}),
    ("aliases-short", "Letter aliases only", "aliases", {"aliases": "short"}),
    ("aliases-mnemonic", "Conventional aliases only", "aliases", {"aliases": "mnemonic"}),
    ("no-dict", "No IRI dictionary", "dictionary", {"dictionary": False}),
    ("no-lists", "No ( … ) RDF lists", "blank nodes", {"lists": False}),
    ("no-inline", "No nested [ … ] blank nodes", "blank nodes", {"inline_bnodes": False}),
    ("no-literal-shorthand", "Quoted typed literals", "literals", {"literal_shorthand": False}),
    ("minimal", "Everything off (flat)", "all", {"layout": "flat", "prefixes": False, "dictionary": False,
                                                  "lists": False, "inline_bnodes": False,
                                                  "literal_shorthand": False}),
    ("legend", "With legend line", "extras", {"legend": True}),
    ("deskolemize", "De-skolemised", "extras", {"deskolemize": True}),
]


def prefixed_graph(records: list, declared: Dict[str, str], guess_namespace: Callable[[str], Tuple[str, str]]) -> Graph:
    """The union graph with a prefix for every namespace written more than once
    (serialised as Turtle, then passed through drop_unused_prefixes).

    rdflib's Turtle writer only invents prefixes for predicates, so subjects and objects in
    other namespaces are written as full IRIs. This is the stronger Turtle baseline: what a
    careful author or tool would write. Prefix names come from the source, or are ns1, ns2…
    """
    graph = Graph()
    counts: Counter = Counter()
    for _g, s, p, o in records:
        graph.add((s, p, o))
        for term in (s, p, o):
            if isinstance(term, URIRef):
                ns, local = guess_namespace(str(term))
                if local:
                    counts[ns] += 1
        if isinstance(o, Literal) and o.datatype is not None:
            counts[guess_namespace(str(o.datatype))[0]] += 1
    bound = {str(ns) for _prefix, ns in graph.namespaces()}
    taken = {prefix for prefix, _ns in graph.namespaces()}
    n = 0
    for ns, uses in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        if uses < 2 or ns in bound:
            continue
        prefix = declared.get(ns)
        if not prefix or prefix in taken:
            n += 1
            while f"ns{n}" in taken:
                n += 1
            prefix = f"ns{n}"
        graph.bind(prefix, URIRef(ns))
        taken.add(prefix)
    return graph


_PREFIX_LINE = re.compile(r"^@prefix ([A-Za-z][\w.-]*|):\s*<[^>]*>\s*\.\s*$")


def drop_unused_prefixes(turtle: str) -> str:
    """Remove the @prefix lines whose prefix the body never uses.

    rdflib declares every namespace it meets, including rdf: when every rdf:type is written
    as ``a`` and xsd: when every typed literal is written bare. A prefix that appears only
    inside a string or an IRI is kept, so the result always parses to the same graph.
    """
    lines = turtle.split("\n")
    body = "\n".join(line for line in lines if not _PREFIX_LINE.match(line))
    def used(line: str) -> bool:
        m = _PREFIX_LINE.match(line)
        return m is None or re.search(r"(?<![\w.-])" + re.escape(m.group(1)) + ":", body) is not None
    return "\n".join(line for line in lines if used(line))


@dataclass
class LoretoResult:
    text: str
    stats: object          # loreto.LoretoStats


class Entry:
    """One parsed input and everything computed from it. The rdflib objects are not
    thread-safe (serialising can bind prefixes), so every use holds ``lock``."""

    _MAX_LORETO = 48

    def __init__(self, loreto: ModuleType, text: str, fmt: str, base: str, ds: Dataset, records: list):
        self.lock = threading.RLock()
        self.loreto_mod = loreto
        self.text = text
        self.format = fmt
        self.base = base
        self.ds = ds
        self.records = records
        self.named_graphs = len({g for g, _s, _p, _o in records if g is not None})
        self._union = None
        self._baselines: Dict[str, Union[str, Exception]] = {}
        self._counts: Dict[Tuple[str, str], int] = {}
        self._loreto: "OrderedDict[Tuple[str, object], LoretoResult]" = OrderedDict()
        self._verified: Dict[Tuple[str, object], Tuple[bool, float]] = {}

    def baseline(self, fid: str) -> str:
        if fid == INPUT_ID:
            return self.text
        if fid not in self._baselines:
            b = _BASELINES[fid]
            try:
                if b.source == "dataset":
                    data = self.ds
                elif b.source == "prefixed":
                    declared = {str(ns): prefix for prefix, ns in self.ds.namespaces()
                                if prefix and not prefix.startswith(("ns", "default"))}
                    data = prefixed_graph(self.records, declared, self.loreto_mod.guess_namespace)
                else:
                    if self._union is None:
                        self._union = self.loreto_mod.records_to_union_graph(self.records)
                    data = self._union
                out = data.serialize(format=b.rdflib_format)
                out = out.decode("utf-8") if isinstance(out, bytes) else out
                self._baselines[fid] = drop_unused_prefixes(out) if b.source == "prefixed" else out
            except Exception as e:  # noqa: BLE001 - e.g. RDF/XML cannot write some predicates
                self._baselines[fid] = e
        value = self._baselines[fid]
        if isinstance(value, Exception):
            raise value
        return value

    def count(self, tk: Tokenizer, fid: str) -> Optional[int]:
        key = (tk.id, fid)
        if key not in self._counts:
            try:
                self._counts[key] = tk.count(self.baseline(fid))
            except Exception:  # noqa: BLE001 - reported by baseline_error
                return None
        return self._counts[key]

    def baseline_error(self, fid: str) -> Optional[str]:
        try:
            self.baseline(fid)
            return None
        except Exception as e:  # noqa: BLE001
            return f"{type(e).__name__}: {e}"[:300]

    def loreto(self, tk: Tokenizer, opts, counter: Optional[RequestCounter] = None) -> LoretoResult:
        key = (tk.id, opts)
        hit = self._loreto.get(key)
        if hit is not None:
            self._loreto.move_to_end(key)
            return hit
        text, stats = self.loreto_mod.serialize(self.ds, opts, return_stats=True,
                                                counter=counter or RequestCounter(tk))
        result = self._loreto[key] = LoretoResult(text, stats)
        while len(self._loreto) > self._MAX_LORETO:
            self._loreto.popitem(last=False)
        return result

    def verify(self, tk: Tokenizer, opts) -> Tuple[bool, float]:
        key = (tk.id, opts)
        if key not in self._verified:
            text = self.loreto(tk, opts).text
            t0 = time.perf_counter()
            ok = bool(self.loreto_mod.verify(self.ds, text, opts))
            self._verified[key] = (ok, round((time.perf_counter() - t0) * 1000, 1))
        return self._verified[key]


class InputCache:
    """The last few parsed inputs, keyed by format, base and content. Two requests for the
    same new input parse it once."""

    def __init__(self, size: int, build: Callable[[str, str, str], Entry]):
        self._size = max(1, size)
        self._build = build
        self._entries: "OrderedDict[str, Entry]" = OrderedDict()
        self._pending: Dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    def get(self, text: str, fmt: str, base: str) -> Entry:
        key = hashlib.sha256(f"{fmt}\x00{base}\x00".encode() + text.encode("utf-8", "surrogatepass")).hexdigest()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
                return entry
            pending = self._pending.setdefault(key, threading.Lock())
        with pending:
            with self._lock:
                entry = self._entries.get(key)
            if entry is not None:
                return entry
            try:
                entry = self._build(text, fmt, base)
            except BaseException:
                with self._lock:
                    self._pending.pop(key, None)
                raise
            with self._lock:
                self._entries[key] = entry
                self._pending.pop(key, None)
                while len(self._entries) > self._size:
                    self._entries.popitem(last=False)
            return entry


class Engine:
    def __init__(self, settings: Settings, registry: Registry, loreto: ModuleType):
        self.settings = settings
        self.registry = registry
        self.loreto = loreto
        self.cache = InputCache(settings.cache_entries, self._build)

    # ------------------------- inputs -------------------------

    def _build(self, text: str, fmt: str, base: str) -> Entry:
        size = len(text.encode("utf-8", "surrogatepass"))
        if size > self.settings.max_input_bytes:
            raise InputError(f"The input has {size / 1048576:.1f} MB; this server accepts up to "
                             f"{self.settings.max_input_bytes / 1048576:.0f} MB.")
        ds, detected = parse_text(text, fmt, base, self.loreto)
        records = self.loreto.dataset_to_records(ds)
        if not records:
            raise InputError("The input parsed, but it contains no triples.")
        if len(records) > self.settings.max_triples:
            raise InputError(f"The input has {len(records):,} triples; this server accepts up to "
                             f"{self.settings.max_triples:,}.")
        return Entry(self.loreto, text, detected, base, ds, records)

    def entry(self, text: str, fmt: str, base: Optional[str]) -> Entry:
        return self.cache.get(text, fmt, base or "")

    def options(self, tk: Tokenizer, options: dict):
        return self.loreto.LoretoOptions(tokenizer=tk.id, **options)

    # ------------------------- views -------------------------

    def _pane(self, tk: Tokenizer, fid: str, label: str, text: str, tokens: Optional[int]) -> dict:
        shown = text[: self.settings.max_view_chars]
        pieces, rest = tk.pieces(shown, self.settings.max_view_tokens)
        return {
            "id": fid,
            "label": label,
            "tokens": tokens,
            "bytes": len(text.encode("utf-8")),
            "chars": len(text),
            "pieces": pieces,               # text = "".join(pieces) + rest
            "rest": rest,
            "text_truncated": len(shown) < len(text),
        }

    def _input_label(self, entry: Entry) -> str:
        return f"Your input ({INPUT_FORMATS.get(entry.format, entry.format).split(' (')[0]})"

    def _baseline_pane(self, entry: Entry, tk: Tokenizer, fid: str) -> dict:
        if fid == INPUT_ID:
            label = self._input_label(entry)
        elif fid in _BASELINES:
            label = _BASELINES[fid].label
        else:
            raise KeyError(f"unknown view {fid!r}")
        error = entry.baseline_error(fid)
        if error:
            return {"id": fid, "label": label, "error": error}
        return self._pane(tk, fid, label, entry.baseline(fid), entry.count(tk, fid))

    # ------------------------- endpoints -------------------------

    def compare(self, text: str, fmt: str, base: Optional[str], tokenizer: Optional[str], options: dict,
                view: str) -> dict:
        tk = self.registry.get(tokenizer)
        entry = self.entry(text, fmt, base)
        with entry.lock:
            opts = self.options(tk, options)
            result = entry.loreto(tk, opts)
            stats = result.stats
            verified, verify_ms = entry.verify(tk, opts)
            loreto_tokens = stats.tokens["chosen"]

            formats = []
            for fid, label, graph_only in ([(INPUT_ID, self._input_label(entry), False)]
                                           + [(b.id, b.label, b.source != "dataset") for b in BASELINES]):
                tokens = entry.count(tk, fid)
                formats.append({
                    "id": fid,
                    "label": label,
                    "tokens": tokens,
                    "bytes": len(entry.baseline(fid).encode("utf-8")) if tokens is not None else None,
                    "saving_pct": _saving(tokens, loreto_tokens),
                    "merges_graphs": graph_only and entry.named_graphs > 0,
                    "error": entry.baseline_error(fid) if tokens is None else None,
                })
            formats.append({"id": LORETO_ID, "label": "Loreto", "tokens": loreto_tokens,
                            "bytes": stats.bytes, "saving_pct": None, "merges_graphs": False, "error": None})

            return {
                "input": {
                    "format": entry.format,
                    "format_label": INPUT_FORMATS.get(entry.format, entry.format),
                    "triples": len(entry.records),
                    "named_graphs": entry.named_graphs,
                    "bytes": len(entry.text.encode("utf-8")),
                },
                "tokenizer": {"id": tk.id, "label": tk.label, "models": tk.models},
                "options": options,
                "loreto": {
                    "tokens": loreto_tokens,
                    "bytes": stats.bytes,
                    "layout": stats.layout,
                    "layout_tokens": {k: v for k, v in stats.tokens.items() if k != "chosen"},
                    "prefixes": stats.prefixes,
                    "dictionary_entries": stats.dictionary_entries,
                    "blank_nodes": stats.blank_nodes,
                    "timings_ms": stats.timings_ms,
                    "verified": verified,
                    "verify_ms": verify_ms,
                    "pane": self._pane(tk, LORETO_ID, "Loreto", result.text, loreto_tokens),
                },
                "formats": formats,
                "view": self._baseline_pane(entry, tk, view),
            }

    def view(self, text: str, fmt: str, base: Optional[str], tokenizer: Optional[str], view: str) -> dict:
        tk = self.registry.get(tokenizer)
        entry = self.entry(text, fmt, base)
        with entry.lock:
            return self._baseline_pane(entry, tk, view)

    def ablation(self, text: str, fmt: str, base: Optional[str], tokenizer: Optional[str]) -> dict:
        tk = self.registry.get(tokenizer)
        entry = self.entry(text, fmt, base)
        counter = RequestCounter(tk)
        rows = []
        with entry.lock:
            full = None
            for vid, label, group, change in ABLATIONS:
                opts = self.options(tk, change)
                t0 = time.perf_counter()
                result = entry.loreto(tk, opts, counter)
                ms = (time.perf_counter() - t0) * 1000
                tokens = result.stats.tokens["chosen"]
                if full is None:
                    full = tokens
                rows.append({
                    "id": vid,
                    "label": label,
                    "group": group,
                    "tokens": tokens,
                    "delta": tokens - full,
                    "delta_pct": round(100.0 * (tokens - full) / full, 2) if full else None,
                    "layout": result.stats.layout,
                    "ms": round(result.stats.timings_ms.get("total", ms), 1),
                })
        return {"tokenizer": {"id": tk.id, "label": tk.label}, "full_tokens": full, "rows": rows}

    def tokenizers(self, text: str, fmt: str, base: Optional[str]) -> dict:
        entry = self.entry(text, fmt, base)
        reference = self.registry.default
        columns = [INPUT_ID, "turtle-prefixed", "turtle", "ntriples", "jsonld", "rdfxml"]
        rows = []
        with entry.lock:
            ref_text = entry.loreto(reference, self.options(reference, {})).text
            for tk in self.registry.all():
                own = entry.loreto(tk, self.options(tk, {})).stats.tokens["chosen"]
                ref_tokens = tk.count(ref_text)
                turtle = entry.count(tk, "turtle-prefixed")
                rows.append({
                    "tokenizer": {"id": tk.id, "label": tk.label, "models": tk.models},
                    "formats": {fid: entry.count(tk, fid) for fid in columns},
                    "loreto": own,
                    "loreto_reference_text": ref_tokens,
                    "saving_vs_turtle_pct": _saving(turtle, own),
                    "reoptimisation_gain_pct": _saving(ref_tokens, own),
                })
        labels = {INPUT_ID: "Your input", **{b.id: b.label for b in BASELINES}}
        return {
            "reference": {"id": reference.id, "label": reference.label},
            "columns": [{"id": c, "label": labels[c]} for c in columns],
            "rows": rows,
        }

    def download(self, text: str, fmt: str, base: Optional[str], tokenizer: Optional[str], options: dict,
                 which: str) -> Tuple[str, str]:
        """Full text of one pane and its file name."""
        tk = self.registry.get(tokenizer)
        entry = self.entry(text, fmt, base)
        with entry.lock:
            if which == LORETO_ID:
                return entry.loreto(tk, self.options(tk, options)).text, "graph.loreto"
            if which == INPUT_ID:
                return entry.text, "input.txt"
            if which in _BASELINES:
                return entry.baseline(which), f"graph.{_BASELINES[which].ext}"
        raise KeyError(f"unknown download {which!r}")


def _saving(baseline: Optional[int], loreto: Optional[int]) -> Optional[float]:
    if not baseline or loreto is None:
        return None
    return round(100.0 * (baseline - loreto) / baseline, 2)
