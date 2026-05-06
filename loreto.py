from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Tuple, Set
from collections import Counter, defaultdict

from rdflib import Dataset, Graph, Namespace, URIRef, BNode, Literal, XSD
from rdflib.namespace import NamespaceManager


# ------------------------- input formats -------------------------

_EXT2FMT = {
    ".ttl":    "turtle",
    ".n3":     "n3",
    ".nt":     "nt",
    ".trig":   "trig",
    ".nq":     "nquads",
    ".rdf":    "xml",
    ".owl":    "xml",
    ".xml":    "xml",
    ".jsonld": "json-ld",
    ".trix":   "trix",
}

_GUESS_FMTS = ["turtle", "n3", "trig", "nt", "nquads", "xml", "json-ld", "trix"]


def parse_to_dataset(input_path: Path) -> Dataset:
    ds = Dataset()
    ext = input_path.suffix.lower()

    if ext in _EXT2FMT:
        ds.parse(location=str(input_path), format=_EXT2FMT[ext])
        return ds

    last_err: Exception | None = None
    for fmt in _GUESS_FMTS:
        try:
            ds.parse(location=str(input_path), format=fmt)
            return ds
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(
        f"Could not parse '{input_path.name}'. Tried formats: {_GUESS_FMTS}. Last error: {last_err}"
    )

NodeTerm = URIRef | BNode
ObjectTerm = URIRef | BNode | Literal
QuadRecord = Tuple[NodeTerm | None, NodeTerm, URIRef, ObjectTerm]


def dataset_to_records(ds: Dataset) -> List[QuadRecord]:
    default_ctx = ds.default_context.identifier
    records: List[QuadRecord] = []

    for s, p, o, ctx in ds.quads((None, None, None, None)):
        ctx_id = getattr(ctx, "identifier", ctx)
        graph_name = None if ctx_id == default_ctx else ctx_id
        records.append((graph_name, s, p, o))

    return records


def records_to_union_graph(records: Iterable[QuadRecord]) -> Graph:
    g = Graph()
    for _ctx, s, p, o in records:
        g.add((s, p, o))
    return g


# ------------------------- token/byte counting -------------------------

class TokenCounter:
    def __init__(self, prefer_tiktoken: bool = True, encoding_name: str = "o200k_base"):
        self._enc = None
        if prefer_tiktoken:
            try:
                import tiktoken  # type: ignore
                try:
                    self._enc = tiktoken.get_encoding(encoding_name)
                except Exception:
                    self._enc = tiktoken.encoding_for_model("gpt-4o")
            except Exception:
                self._enc = None

        self._cache: Dict[str, int] = {}

    def count(self, s: str) -> int:
        v = self._cache.get(s)
        if v is not None:
            return v
        if self._enc is not None:
            v = len(self._enc.encode(s))
        else:
            v = len(s.encode("utf-8"))
        self._cache[s] = v
        return v


# ------------------------- representation utilities -------------------------

_LOCAL_SAFE_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def guess_namespace(iri: str) -> Tuple[str, str]:
    if "#" in iri:
        ns, local = iri.rsplit("#", 1)
        return ns + "#", local
    if "/" in iri:
        ns, local = iri.rsplit("/", 1)
        return ns + "/", local
    return iri, ""


def is_namespace(ns: str) -> bool:
    return ns.endswith("#") or ns.endswith("/")


def escape_local_lossless(local: str) -> str:
    """
    Reversible escaping to preserve losslessness without collisions:
      - if local is safe [A-Za-z0-9._-]+ -> keep it unchanged
      - otherwise encode unsafe bytes byte-by-byte as %HH
    """
    if local == "":
        return ""
    if _LOCAL_SAFE_RE.match(local):
        return local

    b = local.encode("utf-8")
    out: List[str] = []
    for byte in b:
        ch = chr(byte)
        if ("A" <= ch <= "Z") or ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch in "._-":
            out.append(ch)
        else:
            out.append(f"%{byte:02X}")
    return "".join(out) or "_"


def single_char_aliases():
    yield ""  # Empty default prefix for maximum compression
    for ch in "abcdefghijklmnopqrstuvwxyz":
        yield ch
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        yield ch
    i = 0
    while True:
        yield f"p{i}"
        i += 1


def ns_to_prefix_pairs(nm: NamespaceManager) -> List[Tuple[str, str]]:
    pairs = [(str(ns), pfx) for (pfx, ns) in nm.namespaces() if pfx is not None]
    pairs.sort(key=lambda t: len(t[0]), reverse=True)  # longest match first
    return pairs


# ------------------------- 1) prefix planning by benefit -------------------------

def plan_prefixes_by_benefit(records: Iterable[QuadRecord], tok: TokenCounter, max_prefixes: int = 128) -> NamespaceManager:
    """
    Select namespaces for prefixes using net benefit:
      benefit(ns) = sum_{iri in ns} freq(iri) * (cost(<iri>) - cost(a:local_esc))
                    - cost(PREFIX a:<ns>\n)

    Then assign short aliases (empty, a, b, c...) in descending benefit order.
    """
    iri_freq: Counter[str] = Counter()
    ns_to_iris: Dict[str, Set[str]] = defaultdict(set)

    for ctx, s, p, o in records:
        terms = [s, p, o]
        if ctx is not None:
            terms.append(ctx)
        for t in terms:
            if isinstance(t, URIRef):
                iri = str(t)
                if iri == "http://www.w3.org/1999/02/22-rdf-syntax-ns#type":
                    continue
                ns, local = guess_namespace(iri)
                if is_namespace(ns) and local:
                    iri_freq[iri] += 1
                    ns_to_iris[ns].add(iri)

    scored: List[Tuple[float, str]] = []
    for ns, iris in ns_to_iris.items():
        prefix_overhead = tok.count(f"PREFIX a:<{ns}>\n")
        saving = 0
        for iri in iris:
            f = iri_freq[iri]
            _ns, local = guess_namespace(iri)
            local_esc = escape_local_lossless(local)
            saving += f * (tok.count(f"<{iri}>") - tok.count(f"a:{local_esc}"))
        benefit = saving - prefix_overhead
        if benefit > 0:
            scored.append((benefit, ns))

    scored.sort(key=lambda x: x[0], reverse=True)
    selected = [ns for _b, ns in scored[:max_prefixes]]

    nm = NamespaceManager(Graph())
    gen = single_char_aliases()
    used: Set[str] = set()

    for ns in selected:
        alias = next(gen)
        base, k = alias, 1
        while alias in used:
            alias = f"{base}{k}"
            k += 1
        nm.bind(alias, Namespace(ns), replace=False)
        used.add(alias)

    return nm


# ------------------------- 2) I# dictionary by benefit -------------------------

def iri_is_curieable(iri: str, ns2pfx: List[Tuple[str, str]]) -> bool:
    for nsuri, _pfx in ns2pfx:
        if iri.startswith(nsuri) and iri[len(nsuri):] != "":
            return True
    return False


def build_dict_by_benefit(records: Iterable[QuadRecord], nm: NamespaceManager, tok: TokenCounter) -> Dict[str, str]:
    """
    Select non-curieable IRIs for an I# dictionary when they provide net token savings.
    """
    ns2pfx = ns_to_prefix_pairs(nm)

    freq: Counter[str] = Counter()
    for ctx, s, p, o in records:
        terms = [s, p, o]
        if ctx is not None:
            terms.append(ctx)
        for t in terms:
            if isinstance(t, URIRef):
                iri = str(t)
                if iri == "http://www.w3.org/1999/02/22-rdf-syntax-ns#type":
                    continue
                if not iri_is_curieable(iri, ns2pfx):
                    freq[iri] += 1

    candidates: List[Tuple[float, str]] = []
    for iri, f in freq.items():
        ix = "I0"
        direct = f * tok.count(f"<{iri}>")
        indexed = f * tok.count(ix) + tok.count(f"{ix}=<{iri}>\n")
        net = direct - indexed
        if net > 0:
            candidates.append((net, iri))

    candidates.sort(key=lambda x: x[0], reverse=True)

    index_map: Dict[str, str] = {}
    savings_total = 0

    for k, (_net_est, iri) in enumerate(candidates):
        ix = f"I{k}"
        f = freq[iri]
        direct = f * tok.count(f"<{iri}>")
        indexed = f * tok.count(ix) + tok.count(f"{ix}=<{iri}>\n")
        net = direct - indexed
        if net <= 0:
            continue
        index_map[iri] = ix
        savings_total += net

    if not index_map:
        return {}

    if savings_total <= tok.count("DICT\n"):
        return {}

    return index_map


# ------------------------- indexing / rendering -------------------------

class IRIIndex:
    def __init__(self, nm: NamespaceManager, index_map: Dict[str, str]):
        self.nm = nm
        self.index_map = index_map
        self.used_prefixes: Set[str] = set()
        self.used_indices: Set[str] = set()
        self._ns2pfx = ns_to_prefix_pairs(nm)

        self._bmap: Dict[str, str] = {}
        self._bcount = 0

    def iri_to_curie(self, iri: str) -> str | None:
        if iri == "http://www.w3.org/1999/02/22-rdf-syntax-ns#type":
            return "a"
            
        for nsuri, pfx in self._ns2pfx:
            if iri.startswith(nsuri):
                local = iri[len(nsuri):]
                if not local:
                    return None
                local_esc = escape_local_lossless(local)
                self.used_prefixes.add(pfx)
                return f"{pfx}:{local_esc}" if pfx else f":{local_esc}"
        return None

    def repr_literal(self, lit: Literal) -> str:
        # Preserve the exact lexical form of the literal; JSON escaping is reversible.
        val = json.dumps(str(lit))
        
        if lit.datatype and lit.datatype != XSD.string:
            dt = str(lit.datatype)
            dt_cur = self.iri_to_curie(dt)
            if dt_cur is None:
                ix = self.index_map.get(dt)
                if ix:
                    self.used_indices.add(ix)
                    dt_cur = ix
                else:
                    dt_cur = f"<{dt}>"
            return f"{val}^^{dt_cur}"
        if lit.language:
            return f"{val}@{lit.language}"
        return val

    def repr(self, term: URIRef | BNode | Literal) -> str:
        if isinstance(term, BNode):
            k = str(term)
            lbl = self._bmap.get(k)
            if lbl is None:
                lbl = f"_b{self._bcount}"
                self._bcount += 1
                self._bmap[k] = lbl
            return lbl

        if isinstance(term, Literal):
            return self.repr_literal(term)

        iri = str(term)
        if iri == "http://www.w3.org/1999/02/22-rdf-syntax-ns#type":
            return "a"

        cur = self.iri_to_curie(iri)
        if cur is not None:
            return cur

        ix = self.index_map.get(iri)
        if ix:
            self.used_indices.add(ix)
            return ix

        return f"<{iri}>"


# ------------------------- 3) emission: minimal header + variants -------------------------

def emit_header(nm: NamespaceManager, used_prefixes: Set[str], index_map: Dict[str, str], used_indices: Set[str]) -> List[str]:
    lines: List[str] = []

    if used_prefixes:
        pairs = [(pfx, str(ns)) for (pfx, ns) in nm.namespaces() if pfx in used_prefixes]
        for pfx, ns in sorted(pairs, key=lambda x: x[0]):
            lines.append(f"PREFIX {pfx}:<{ns}>")  # Standard SPARQL syntax

    if used_indices:
        lines.append("DICT")
        inv = {ix: iri for iri, ix in index_map.items() if ix in used_indices}
        for ix in sorted(inv.keys(), key=lambda s: int(s[1:])):
            lines.append(f"{ix}=<{inv[ix]}>")

    return lines


def emit_flat(records: Iterable[QuadRecord], idx: IRIIndex) -> List[str]:
    quads = []
    for ctx, s, p, o in records:
        quads.append((
            "" if ctx is None else idx.repr(ctx),
            idx.repr(s),
            idx.repr(p),
            idx.repr(o),
        ))

    quads.sort(key=lambda q: (q[0], q[1], q[2], q[3]))
    out: List[str] = []
    for ctx, s, p, o in quads:
        if ctx:
            out.append(f"GRAPH {ctx} {s} {p} {o}")
        else:
            out.append(f"{s} {p} {o}")
    return out


def _emit_grouped_graph(records: List[Tuple[NodeTerm, URIRef, ObjectTerm]], idx: IRIIndex) -> List[str]:
    # 1. Graph profiling for blank-node inline detection
    bnode_in_degree = Counter()
    bnode_is_obj = set()
    for s, p, o in records:
        if isinstance(o, BNode):
            bnode_in_degree[o] += 1
            bnode_is_obj.add(o)

    inline_bnodes = {b for b in bnode_is_obj if bnode_in_degree[b] == 1}
    raw_buckets = defaultdict(lambda: defaultdict(list))
    for s, p, o in records:
        raw_buckets[s][p].append(o)
        
    def semantic_sort_key(p_raw):
        # rdf:type gets highest priority; everything else is sorted lexically
        return (0, "") if str(p_raw) == "http://www.w3.org/1999/02/22-rdf-syntax-ns#type" else (1, idx.repr(p_raw))

    def render_node(n, depth=0) -> str:
        # Limit recursion in case of unusual cyclic blank-node traces
        if isinstance(n, BNode) and n in inline_bnodes and depth < 10:
            if not raw_buckets[n]:
                return "[]"
            preds = sorted(raw_buckets[n].keys(), key=semantic_sort_key)
            out_p = []
            for p_raw in preds:
                p_str = idx.repr(p_raw)
                seen_objs = set()
                uniq_objs = []
                for o_raw in raw_buckets[n][p_raw]:
                    r = render_node(o_raw, depth + 1)
                    if r not in seen_objs:
                        seen_objs.add(r)
                        uniq_objs.append(r)
                objs_str = ", ".join(uniq_objs)
                out_p.append(f"{p_str} {objs_str}")
            return "[ " + " ; ".join(out_p) + " ]"
        return idx.repr(n)

    out: List[str] = []
    
    # 2. Iterate over non-inlined subjects
    def node_sort_key(s_raw):
        return idx.repr(s_raw)

    for s_raw in sorted(raw_buckets.keys(), key=node_sort_key):
        if isinstance(s_raw, BNode) and s_raw in inline_bnodes:
            continue

        out.append(idx.repr(s_raw))
        preds = sorted(raw_buckets[s_raw].keys(), key=semantic_sort_key)
        
        for i, p_raw in enumerate(preds):
            p_str = idx.repr(p_raw)
            seen_objs = set()
            uniq_objs = []
            for o_raw in raw_buckets[s_raw][p_raw]:
                r = render_node(o_raw, 0)
                if r not in seen_objs:
                    seen_objs.add(r)
                    uniq_objs.append(r)
                    
            objs_str = ", ".join(uniq_objs)
            end_char = " ;" if i < len(preds) - 1 else ""
            out.append(f" {p_str} {objs_str}{end_char}")
            
    return out


def emit_grouped_by_subject(records: Iterable[QuadRecord], idx: IRIIndex) -> List[str]:
    by_ctx: Dict[NodeTerm | None, List[Tuple[NodeTerm, URIRef, ObjectTerm]]] = defaultdict(list)
    for ctx, s, p, o in records:
        by_ctx[ctx].append((s, p, o))

    out: List[str] = []
    ordered_contexts = sorted(by_ctx.keys(), key=lambda ctx: "" if ctx is None else idx.repr(ctx))

    for ctx in ordered_contexts:
        if ctx is not None:
            out.append(f"GRAPH {idx.repr(ctx)}")
        out.extend(_emit_grouped_graph(by_ctx[ctx], idx))

    return out


# ------------------------- 4) cost and minimum selection -------------------------

def build_variant_text(nm: NamespaceManager, index_map: Dict[str, str], body_fn, records: List[QuadRecord]) -> str:
    idx = IRIIndex(nm, index_map)
    body = body_fn(records, idx)
    header = emit_header(nm, idx.used_prefixes, index_map, idx.used_indices)
    lines = header + body
    return ("\n".join(lines) + "\n") if lines else ""


# ------------------------- 5) main conversion and CLI -------------------------

def convert_to_loreto_lite(input_path: Path, emit_ttl: bool, tok: TokenCounter, max_prefixes: int) -> Tuple[str, str | None]:
    ds = parse_to_dataset(input_path)
    records = dataset_to_records(ds)
    g = records_to_union_graph(records)

    ttl_text: str | None = None
    if emit_ttl:
        ttl = g.serialize(format="turtle")
        if isinstance(ttl, bytes):
            ttl = ttl.decode("utf-8")
        ttl_text = ttl

    nm = plan_prefixes_by_benefit(records, tok, max_prefixes=max_prefixes)
    index_map = build_dict_by_benefit(records, nm, tok)

    text_flat = build_variant_text(nm, index_map, emit_flat, records)
    text_group = build_variant_text(nm, index_map, emit_grouped_by_subject, records)

    best = text_flat if tok.count(text_flat) <= tok.count(text_group) else text_group
    return best, ttl_text


def main() -> int:
    ap = argparse.ArgumentParser(description="RDF/OWL → loreto (token-minimal, LLM-friendly), without JSON-LD")
    ap.add_argument("input", type=str, help="Path to an RDF/OWL file (.ttl/.n3/.rdf/.owl/.nt/.trig/.nq/...)")
    ap.add_argument("-o", "--output", type=str, default=None, help="Output path for the .loreto file (optional)")
    ap.add_argument("--no-ttl", action="store_true", help="Do not emit the .normalized.ttl file")
    ap.add_argument("--max-prefixes", type=int, default=128, help="Maximum number of candidate prefixes (default: 128)")
    ap.add_argument("--no-tiktoken", action="store_true", help="Force UTF-8 byte counting instead of token counting")
    args = ap.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        raise SystemExit(f"[ERROR] Does not exist: {in_path}")

    tok = TokenCounter(prefer_tiktoken=not args.no_tiktoken)

    # --- STATS (initial input tokens) ---
    raw_bytes = in_path.read_bytes()
    raw_text = raw_bytes.decode("utf-8", errors="replace")
    input_cost = tok.count(raw_text)
    input_bytes = len(raw_bytes)

    try:
        best_text, ttl_text = convert_to_loreto_lite(
            in_path,
            emit_ttl=not args.no_ttl,
            tok=tok,
            max_prefixes=args.max_prefixes,
        )
    except Exception as e:
        raise SystemExit(f"[ERROR] {e}")

    out_path = Path(args.output) if args.output else in_path.with_name(in_path.stem + ".loreto")
    out_path.write_text(best_text, encoding="utf-8")

    if ttl_text is not None:
        ttl_path = in_path.with_name(in_path.stem + ".normalized.ttl")
        ttl_path.write_text(ttl_text, encoding="utf-8")

    # --- STATS (final .loreto tokens) ---
    output_cost = tok.count(best_text)
    output_bytes = len(best_text.encode("utf-8"))
    ratio = (output_cost / input_cost) if input_cost > 0 else 0.0

    print("[STATS] input_tokens/bytes :", input_cost, "/", input_bytes)
    print("[STATS] output_tokens/bytes:", output_cost, "/", output_bytes)
    print(f"[STATS] reduction_tokens   : {input_cost - output_cost} ({(1.0 - ratio) * 100.0:.2f}%)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
