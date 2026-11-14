from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple, Union

from rdflib import BNode, Dataset, Graph, Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import RDF, XSD

__version__ = "2.0.0"

__all__ = [
    "serialize", "parse", "verify", "LoretoOptions", "LoretoStats", "LoretoSyntaxError",
    "TokenCounter", "parse_to_dataset", "dataset_to_records", "records_to_union_graph",
    "__version__",
]

NodeTerm = Union[URIRef, BNode]
ObjectTerm = Union[URIRef, BNode, Literal]
QuadRecord = Tuple[Optional[NodeTerm], NodeTerm, URIRef, ObjectTerm]   # (graph, s, p, o); graph None = default graph

XSD_STRING = str(XSD.string)
_XSD = "http://www.w3.org/2001/XMLSchema#"
_SHORTHAND = {   # Turtle's bare literal forms, applied only when the lexical form matches exactly
    _XSD + "integer": re.compile(r"[+-]?[0-9]+"),
    _XSD + "decimal": re.compile(r"[+-]?[0-9]*\.[0-9]+"),
    _XSD + "double": re.compile(r"[+-]?(?:[0-9]+\.[0-9]*[eE][+-]?[0-9]+|\.[0-9]+[eE][+-]?[0-9]+|[0-9]+[eE][+-]?[0-9]+)"),
    _XSD + "boolean": re.compile(r"true|false"),
}


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

_UNSUPPORTED_EXT = {".owx": "OWL/XML", ".omn": "OWL Manchester syntax", ".ofn": "OWL functional syntax"}


def parse_to_dataset(input_path: Path) -> Dataset:
    """Parse an RDF file into a Dataset: the extension's format first, then the others."""
    input_path = Path(input_path)
    ext = input_path.suffix.lower()
    if ext in _UNSUPPORTED_EXT:
        raise RuntimeError(f"'{input_path.name}' looks like {_UNSUPPORTED_EXT[ext]}, which rdflib cannot read; "
                           "convert it to RDF/XML or Turtle first (e.g. with ROBOT or the OWL API).")
    first = _EXT2FMT.get(ext)
    fmts = ([first] if first else []) + [f for f in _GUESS_FMTS if f != first]
    errors: List[str] = []
    for fmt in fmts:
        ds = Dataset()   # a fresh dataset per attempt: a failed parse may leave partial data behind
        try:
            ds.parse(location=str(input_path), format=fmt)
            return ds
        except Exception as e:  # noqa: BLE001 - try the next format
            errors.append(f"{fmt}: {type(e).__name__}: {e}")
    hint = ""
    try:
        if "<Ontology" in input_path.read_text(encoding="utf-8", errors="replace")[:4096]:
            hint = " The file looks like OWL/XML, which rdflib cannot read; convert it to RDF/XML or Turtle first."
    except OSError:
        pass
    raise RuntimeError(f"Could not parse '{input_path.name}'. Tried formats: {fmts}.{hint}\n  " + "\n  ".join(errors[:3]))


def _default_graph(ds: Graph) -> Graph:
    g = getattr(ds, "default_graph", None)   # rdflib >= 7.2 deprecates default_context
    return g if isinstance(g, Graph) else ds.default_context


def dataset_to_records(ds: Graph) -> List[QuadRecord]:
    default_ctx = _default_graph(ds).identifier
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


_AUTO_PREFIX_RE = re.compile(r"(?:ns|default)[0-9]*")   # prefixes rdflib invents, not chosen by anyone


def _as_records(data) -> Tuple[List[QuadRecord], Dict[str, str]]:
    """Normalise the input to unique quads, plus the prefixes declared in it (namespace -> prefix)."""
    declared: Dict[str, str] = {}
    if isinstance(data, Graph):
        for pfx, ns in sorted(data.namespaces(), key=lambda t: (t[0], str(t[1]))):
            if pfx and _alias_ok(pfx) and not _AUTO_PREFIX_RE.fullmatch(pfx):
                declared.setdefault(str(ns), pfx)
        if getattr(data, "context_aware", False):
            records = dataset_to_records(data)
        else:
            records = [(None, s, p, o) for s, p, o in data]
    else:
        records = []
        for t in data:
            if len(t) == 3:
                records.append((None, t[0], t[1], t[2]))
            elif len(t) == 4:
                records.append((t[0], t[1], t[2], t[3]))   # (graph, s, p, o), as in dataset_to_records
            else:
                raise ValueError(f"expected triples (s, p, o) or quads (g, s, p, o), got {t!r}")
    return list(dict.fromkeys(records)), declared


# ------------------------- tokenisers -------------------------

class TokenCounter:


    _CACHE_MAX_LEN = 512

    def __init__(self, spec: str = "o200k_base"):
        self.spec = spec
        self._encode = self._load(spec)
        self._cache: Dict[str, int] = {}

    @staticmethod
    def _load(spec: str) -> Callable[[str], int]:
        if spec == "bytes":
            return lambda s: len(s.encode("utf-8"))
        if spec == "chars":
            return len
        local_file = spec[len("hf-file:"):] if spec.startswith("hf-file:") else (
            spec if spec.lower().endswith(".json") else None)
        if local_file is not None or spec.startswith("hf:"):
            if local_file is not None and not Path(local_file).is_file():
                raise FileNotFoundError(f"tokenizer file not found: {local_file}")
            try:
                from tokenizers import Tokenizer  # type: ignore
            except ImportError as e:
                raise ImportError(f"tokenizer '{spec}' needs the 'tokenizers' package: pip install tokenizers") from e
            if local_file is not None:
                tk = Tokenizer.from_file(local_file)
            else:
                tk = Tokenizer.from_pretrained(spec[len("hf:"):])
            return lambda s: len(tk.encode(s, add_special_tokens=False).ids)
        try:
            import tiktoken  # type: ignore
        except ImportError as e:
            raise ImportError(f"tokenizer '{spec}' needs the 'tiktoken' package (pip install tiktoken); "
                              "use tokenizer='bytes' to count UTF-8 bytes on purpose") from e
        if spec.startswith("tiktoken:"):
            enc = tiktoken.encoding_for_model(spec[len("tiktoken:"):])
        else:
            enc = tiktoken.get_encoding(spec)
        return lambda s: len(enc.encode(s, disallowed_special=()))

    @property
    def name(self) -> str:
        return self.spec

    def count(self, s: str) -> int:
        if len(s) > self._CACHE_MAX_LEN:
            return self._encode(s)
        v = self._cache.get(s)
        if v is None:
            v = self._cache[s] = self._encode(s)
        return v


# ------------------------- options and statistics -------------------------

_LAYOUTS = ("auto", "flat", "grouped")
_ALIAS_POLICIES = ("auto", "short", "mnemonic")


@dataclass(frozen=True)
class LoretoOptions:
    """Serialisation options. The defaults are the full method; each switch turns one
    transformation off, which is what an ablation study needs."""
    tokenizer: str = "o200k_base"   # cost function, see TokenCounter
    layout: str = "auto"            # auto (cheaper of the two) | flat | grouped
    prefixes: bool = True           # namespace prefixes: PREFIX p:<ns>
    id_prefixes: bool = True        # OBO-style identifier spaces as prefixes: GO:0008150
    aliases: str = "auto"           # auto (cheapest, mnemonic on ties) | short | mnemonic
    dictionary: bool = True         # DICT entries I<n>=<iri> for IRIs without a CURIE
    lists: bool = True              # ( ... ) for RDF lists, () for rdf:nil
    literal_shorthand: bool = True  # bare integers, decimals, doubles and booleans
    inline_bnodes: bool = True      # [ ... ] for blank nodes used once, [] for unreferenced ones
    deskolemize: bool = False       # .well-known/genid IRIs back to blank nodes (drops those IRIs)
    legend: bool = False            # one '#' line describing the syntax actually used
    max_prefixes: int = 128

    def __post_init__(self):
        if self.layout not in _LAYOUTS:
            raise ValueError(f"layout must be one of {_LAYOUTS}, got {self.layout!r}")
        if self.aliases not in _ALIAS_POLICIES:
            raise ValueError(f"aliases must be one of {_ALIAS_POLICIES}, got {self.aliases!r}")
        if self.max_prefixes < 0:
            raise ValueError("max_prefixes must be >= 0")


@dataclass
class LoretoStats:
    tokenizer: str
    layout: str
    triples: int
    named_graphs: int
    tokens: Dict[str, int]          # tokens of every layout rendered, plus "chosen"
    bytes: int
    prefixes: int
    dictionary_entries: int
    blank_nodes: Dict[str, int]
    timings_ms: Dict[str, float]

    def as_dict(self) -> Dict:
        return asdict(self)


# ------------------------- aliases and namespaces -------------------------

_RESERVED_ALIASES = frozenset({"a", "_", "true", "false", "PREFIX", "DICT", "GRAPH", "I"})
_ALIAS_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,15}")
_INDEX_RE = re.compile(r"I[0-9]+")
_LETTERS = list("bcdefghijklmnopqrstuvwxyz") + list("ABCDEFGHJKLMNOPQRSTUVWXYZ")
_LOCAL_RE = re.compile(r"[\w.\-]+")
_OBO_ID_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*)_([0-9]+)")
_EXT_RE = re.compile(r"\.(?:owl|rdf|rdfs|ttl|n3|nt|nq|xml|jsonld|trig|obo)$", re.I)

_WELL_KNOWN_PREFIXES = {
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#": "rdf",
    "http://www.w3.org/2000/01/rdf-schema#": "rdfs",
    "http://www.w3.org/2002/07/owl#": "owl",
    "http://www.w3.org/2001/XMLSchema#": "xsd",
    "http://www.w3.org/2004/02/skos/core#": "skos",
    "http://www.w3.org/2008/05/skos-xl#": "skosxl",
    "http://purl.org/dc/elements/1.1/": "dc",
    "http://purl.org/dc/terms/": "dcterms",
    "http://xmlns.com/foaf/0.1/": "foaf",
    "http://schema.org/": "schema",
    "https://schema.org/": "schema",
    "http://www.w3.org/ns/prov#": "prov",
    "http://www.w3.org/ns/shacl#": "sh",
    "http://rdfs.org/ns/void#": "void",
    "http://www.w3.org/ns/dcat#": "dcat",
    "http://www.w3.org/ns/org#": "org",
    "http://www.w3.org/2006/time#": "time",
    "http://www.w3.org/2006/vcard/ns#": "vcard",
    "http://www.w3.org/2003/01/geo/wgs84_pos#": "geo",
    "http://www.w3.org/ns/sosa/": "sosa",
    "http://www.w3.org/ns/ssn/": "ssn",
    "http://purl.org/linked-data/cube#": "qb",
    "http://purl.org/vocab/vann/": "vann",
    "http://purl.org/ontology/bibo/": "bibo",
    "http://purl.obolibrary.org/obo/": "obo",
    "http://www.geneontology.org/formats/oboInOwl#": "oboInOwl",
    "http://www.w3.org/2003/11/swrl#": "swrl",
    "http://www.w3.org/2003/11/swrlb#": "swrlb",
}


def _alias_ok(name: str) -> bool:
    return bool(_ALIAS_RE.fullmatch(name)) and name not in _RESERVED_ALIASES and not _INDEX_RE.fullmatch(name)


def guess_namespace(iri: str) -> Tuple[str, str]:
    if "#" in iri:
        ns, local = iri.rsplit("#", 1)
        return ns + "#", local
    if "/" in iri:
        ns, local = iri.rsplit("/", 1)
        return ns + "/", local
    return iri, ""


def _namespace_splits(iri: str, id_prefixes: bool) -> List[Tuple[str, str]]:
    start = iri.find("://")
    start = start + 3 if start >= 0 else 0
    out: List[Tuple[str, str]] = []
    for sep in ("#", "/"):
        i = iri.rfind(sep)
        if i < start:
            continue
        ns, local = iri[: i + 1], iri[i + 1:]
        if not local or not _LOCAL_RE.fullmatch(local) or (ns, local) in out:
            continue
        out.append((ns, local))
        if id_prefixes:
            m = _OBO_ID_RE.fullmatch(local)
            if m:
                out.append((ns + m.group(1) + "_", m.group(2)))
    return out


def _derive_alias(ns: str) -> Optional[str]:
    if ns.endswith("_"):
        seg = re.split(r"[/#]", ns[:-1])[-1]
        return seg if _alias_ok(seg) else None
    m = re.match(r"[A-Za-z][A-Za-z0-9+.-]*://([^/#?]*)", ns)
    host, path = (m.group(1), ns[m.end():]) if m else ("", ns)
    for seg in reversed([s for s in re.split(r"[/#]", path) if s]):
        seg = _EXT_RE.sub("", seg)
        if _alias_ok(seg):
            return seg
        caps = "".join(ch for ch in seg if ch.isupper())
        if len(seg) > 16 and len(caps) >= 2 and _alias_ok(caps):
            return caps
    labels = [label for label in host.split(".") if label and label.lower() != "www"]
    if len(labels) >= 2 and _alias_ok(labels[-2]):
        return labels[-2]
    return None


def _mnemonics(ns: str, declared: Dict[str, str]) -> List[str]:
    out: List[str] = []
    for name in (_WELL_KNOWN_PREFIXES.get(ns), declared.get(ns), _derive_alias(ns)):
        if name and _alias_ok(name) and name not in out:
            out.append(name)
    return out


_IRI_ESCAPE_RE = re.compile(r'[\x00-\x20<>"{}|^`\\]')
_UCHAR_RE = re.compile(r"\\u([0-9A-Fa-f]{4})|\\U([0-9A-Fa-f]{8})")


def _iri_ref(iri: str) -> str:
    return "<" + _IRI_ESCAPE_RE.sub(lambda m: f"\\u{ord(m.group()):04X}", iri) + ">"


def _unescape_iri(s: str) -> str:
    return _UCHAR_RE.sub(lambda m: chr(int(m.group(1) or m.group(2), 16)), s)


def _json_str(s: str) -> str:
    out = json.dumps(s, ensure_ascii=False)
    try:
        out.encode("utf-8")
    except UnicodeEncodeError:   # lone surrogates: keep them as \u escapes
        out = json.dumps(s)
    return out


def _shorthand(datatype: str, lexical: str) -> bool:
    rx = _SHORTHAND.get(datatype)
    return bool(rx and rx.fullmatch(lexical))


# ------------------------- dataset analysis (blank nodes, lists) -------------------------

_MAX_INLINE_DEPTH = 48


@dataclass
class _Analysis:
    out: Dict[Tuple[Optional[NodeTerm], NodeTerm], Dict[URIRef, List[ObjectTerm]]]
    bnodes: Set[BNode]
    inline: Set[BNode]        # rendered as [ ... ] inside their only parent
    list_heads: Set[BNode]    # rendered as ( ... )
    list_nodes: Set[BNode]    # every node absorbed by list syntax, heads included
    anonymous: Set[BNode]     # unreferenced subjects, rendered as []
    colour: Dict[BNode, str]  # canonical colour, used to number the labelled ones
    first_seen: Dict[BNode, int]


def _analyse(records: List[QuadRecord], opts: LoretoOptions) -> _Analysis:
    out: Dict = defaultdict(lambda: defaultdict(list))
    bnodes: Set[BNode] = set()
    graph_names: Set[BNode] = set()
    contexts: Dict[BNode, Set] = defaultdict(set)
    in_degree: Counter = Counter()
    parent: Dict[BNode, Tuple] = {}
    first_seen: Dict[BNode, int] = {}

    for i, (g, s, p, o) in enumerate(records):
        if isinstance(s, Literal) or not isinstance(p, URIRef) or isinstance(g, Literal):
            raise ValueError(f"not an RDF quad (literal subject/graph or non-IRI predicate): {(g, s, p, o)!r}")
        out[(g, s)][p].append(o)
        for t in (g, s, o):
            if isinstance(t, BNode) and t not in first_seen:
                first_seen[t] = i
                bnodes.add(t)
        if isinstance(g, BNode):
            graph_names.add(g)
        if isinstance(s, BNode):
            contexts[s].add(g)
        if isinstance(o, BNode):
            contexts[o].add(g)
            in_degree[o] += 1
            parent[o] = (g, s, p)

    inline: Set[BNode] = set()
    heads: Set[BNode] = set()
    members: Set[BNode] = set()
    anonymous: Set[BNode] = set()
    if opts.inline_bnodes:
        # Inline only what can be written once, in one place: one graph, one incoming edge,
        # not a graph name, not part of a cycle and not nested too deep.
        cand = {b for b in bnodes
                if b not in graph_names and len(contexts[b]) == 1 and in_degree[b] == 1 and parent[b][1] != b}
        _drop_cycles(cand, parent)
        if opts.lists:
            heads, members = _find_lists(cand, parent, out)
        _limit_depth(cand, parent, out, heads, members)
        inline = cand - members
        anonymous = {b for b in bnodes if b not in graph_names and len(contexts[b]) == 1 and in_degree[b] == 0}

    return _Analysis(out, bnodes, inline, heads, members, anonymous, _bnode_colours(records, bnodes), first_seen)


def _drop_cycles(cand: Set[BNode], parent: Dict) -> None:
    state: Dict[BNode, int] = {}
    cyclic: Set[BNode] = set()
    for b in list(cand):
        path, x = [], b
        while x in cand and x not in state:
            state[x] = 1
            path.append(x)
            x = parent[x][1]
        if x in cand and state.get(x) == 1:
            cyclic.update(path[path.index(x):])
        for y in path:
            state[y] = 2
    cand -= cyclic


def _list_shape(n: BNode, g, out) -> Optional[Tuple[ObjectTerm, ObjectTerm]]:
    po = out.get((g, n))
    if not po or len(po) != 2:
        return None
    first, rest = po.get(RDF.first), po.get(RDF.rest)
    if not first or not rest or len(first) != 1 or len(rest) != 1:
        return None
    if rest[0] != RDF.nil and not isinstance(rest[0], BNode):
        return None
    return first[0], rest[0]


def _find_lists(cand: Set[BNode], parent: Dict, out) -> Tuple[Set[BNode], Set[BNode]]:
    """Well-formed RDF lists: chains of nested blank nodes with exactly one rdf:first and
    one rdf:rest each, ending in rdf:nil."""
    shape = {}
    for n in cand:
        sh = _list_shape(n, parent[n][0], out)
        if sh is not None:
            shape[n] = sh
    heads: Set[BNode] = set()
    members: Set[BNode] = set()
    for n in shape:
        _g, pn, pp = parent[n]
        if pp == RDF.rest and pn in shape:
            continue   # continuation of the parent's list, handled from its head
        chain, x, ok = [n], shape[n][1], True
        while x != RDF.nil:
            if x not in shape:
                ok = False
                break
            chain.append(x)
            x = shape[x][1]
        if ok:
            heads.add(n)
            members.update(chain)
    return heads, members


def _limit_depth(cand: Set[BNode], parent: Dict, out, heads: Set[BNode], members: Set[BNode]) -> None:
    children: Dict = defaultdict(list)
    for b in cand:
        children[parent[b][1]].append(b)
    stack = [(b, 1) for b in cand if parent[b][1] not in cand]
    while stack:
        b, depth = stack.pop()
        if depth > _MAX_INLINE_DEPTH:
            cand.discard(b)
            if b in heads:   # the list continues from the next node
                heads.discard(b)
                members.discard(b)
                nxt = out[(parent[b][0], b)][RDF.rest][0]
                if nxt != RDF.nil:
                    heads.add(nxt)
            depth = 0
        for c in children[b]:
            stack.append((c, depth + (0 if c in members and c not in heads else 1)))


def _bnode_colours(records: List[QuadRecord], bnodes: Set[BNode]) -> Dict[BNode, str]:
    if not bnodes:
        return {}
    incident: Dict[BNode, List[int]] = defaultdict(list)
    keys = []
    for i, quad in enumerate(records):
        keys.append(tuple("" if t is None else (None if isinstance(t, BNode) else t.n3()) for t in quad))
        for t in (quad[0], quad[1], quad[3]):
            if isinstance(t, BNode):
                incident[t].append(i)
    colour = {b: "" for b in bnodes}
    classes = 1
    for _ in range(64):
        new = {}
        for b in bnodes:
            sig = []
            for i in incident[b]:
                sig.append("\x1f".join(
                    "@" if t == b else (colour[t] if isinstance(t, BNode) else keys[i][j])
                    for j, t in enumerate(records[i])))
            sig.sort()
            new[b] = hashlib.sha1((colour[b] + "\x1e" + "\x1e".join(sig)).encode("utf-8")).hexdigest()
        n = len(set(new.values()))
        colour = new
        if n == classes:
            break
        classes = n
    return colour


# ------------------------- planning: prefixes, aliases, dictionary -------------------------

@dataclass
class _Plan:
    prefixes: Dict[str, str] = field(default_factory=dict)   # alias -> namespace
    text: Dict[str, str] = field(default_factory=dict)       # IRI -> CURIE or dictionary index
    alias_of: Dict[str, str] = field(default_factory=dict)   # IRI -> alias used by its CURIE
    index: Dict[str, str] = field(default_factory=dict)      # dictionary index -> IRI


class _Uses:
    
    def __init__(self):
        self.total: Counter = Counter()
        self.line_start: Counter = Counter()

    def add(self, iri: str, line_start: bool = False) -> None:
        self.total[iri] += 1
        if line_start:
            self.line_start[iri] += 1

    def cost(self, tok: TokenCounter, iri: str, text: str) -> int:
        """Tokens spent writing every occurrence of ``iri`` as ``text``."""
        head = self.line_start[iri]
        return head * tok.count(text) + (self.total[iri] - head) * tok.count(" " + text)


def _occurrences(records: List[QuadRecord], an: _Analysis, layout: str, opts: LoretoOptions) -> _Uses:
    uses = _Uses()

    def add(t, line_start: bool = False):
        if isinstance(t, URIRef):
            uses.add(str(t), line_start)
        elif isinstance(t, Literal) and t.datatype is not None and not t.language:
            dt = str(t.datatype)
            if dt != XSD_STRING and not (opts.literal_shorthand and _shorthand(dt, str(t))):
                uses.add(dt)

    def add_object(o):
        if not (opts.lists and o == RDF.nil):
            add(o)

    if layout == "grouped":
        seen_graphs, seen_subjects, seen_predicates = set(), set(), set()
        for g, s, p, o in records:
            if g is not None and g not in seen_graphs:
                seen_graphs.add(g)
                add(g)
            if s in an.list_nodes:
                if p == RDF.first:
                    add_object(o)
                continue
            if (g, s) not in seen_subjects:
                seen_subjects.add((g, s))
                add(s, line_start=True)      # subject lines have no leading space
            if (g, s, p) not in seen_predicates:
                seen_predicates.add((g, s, p))
                if p != RDF.type:
                    add(p)
            add_object(o)
    else:
        for g, s, p, o in records:
            if g is not None:
                add(g)
            add(s, line_start=g is None)     # "s p o", or "GRAPH g s p o"
            if p != RDF.type:
                add(p)
            add_object(o)
    return uses


def _plan(uses: _Uses, opts: LoretoOptions, tok: TokenCounter, declared: Dict[str, str]) -> _Plan:
    plan = _Plan()
    if not uses.total:
        return plan
    full = {iri: uses.cost(tok, iri, _iri_ref(iri)) for iri in uses.total}
    if opts.prefixes and opts.max_prefixes > 0:
        _plan_prefixes(uses, full, opts, tok, declared, plan)
    if opts.dictionary:
        _plan_dictionary(uses, full, tok, plan)
    return plan


def _plan_prefixes(uses: _Uses, full: Dict[str, int], opts: LoretoOptions, tok: TokenCounter,
                   declared: Dict[str, str], plan: _Plan) -> None:
    splits = {iri: _namespace_splits(iri, opts.id_prefixes) for iri in uses.total}
    members: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for iri, candidates in splits.items():
        for ns, local in candidates:
            members[ns].append((iri, local))

    def est(iri: str, local: str) -> int:   # cost of a CURIE before its alias is known
        return uses.cost(tok, iri, "x:" + local)

    current = dict(full)

    def gain(ns: str) -> int:
        total = -tok.count(f"PREFIX x:{_iri_ref(ns)}\n")
        for iri, local in members[ns]:
            d = current[iri] - est(iri, local)
            if d > 0:
                total += d
        return total

    # Lazy greedy: gains only decrease as namespaces are selected, so a stored gain is an
    # upper bound and the top of the heap only needs re-evaluation.
    heap = [(-gain(ns), ns) for ns in sorted(members)]
    heapq.heapify(heap)
    selected: List[str] = []
    while heap and len(selected) < opts.max_prefixes and -heap[0][0] > 0:
        neg, ns = heapq.heappop(heap)
        g = gain(ns)
        if g <= 0:
            continue
        if g < -neg:
            heapq.heappush(heap, (-g, ns))
            continue
        selected.append(ns)
        for iri, local in members[ns]:
            current[iri] = min(current[iri], est(iri, local))

    chosen = set(selected)
    owned: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for iri, candidates in splits.items():
        best = None
        for ns, local in candidates:
            if ns in chosen:
                key = (est(iri, local), -len(ns), ns)
                if best is None or key < best[0]:
                    best = (key, ns, local)
        if best is not None and best[0][0] < full[iri]:
            owned[best[1]].append((iri, best[2]))

    used: Set[str] = set()
    for ns in sorted(owned, key=lambda n: (-sum(uses.total[i] for i, _ in owned[n]), n)):
        alias = _choose_alias(ns, owned[ns], uses, full, tok, opts.aliases, declared, used)
        if alias is not None:
            used.add(alias)
            plan.prefixes[alias] = ns

    alias_of_ns = {ns: a for a, ns in plan.prefixes.items()}
    for iri, candidates in splits.items():
        best = None
        for ns, local in sorted(candidates, key=lambda t: -len(t[0])):
            alias = alias_of_ns.get(ns)
            if alias is None:
                continue
            cost = uses.cost(tok, iri, f"{alias}:{local}")
            if best is None or cost < best[0]:
                best = (cost, f"{alias}:{local}", alias)
        if best is not None and best[0] <= full[iri]:   # on a tie the CURIE wins: it is easier to read
            plan.text[iri] = best[1]
            plan.alias_of[iri] = best[2]


def _choose_alias(ns: str, owned: List[Tuple[str, str]], uses: _Uses, full: Dict[str, int],
                  tok: TokenCounter, policy: str, declared: Dict[str, str], used: Set[str]) -> Optional[str]:
    mnemonics = [m for m in _mnemonics(ns, declared) if m not in used]
    if policy == "mnemonic" and mnemonics:
        candidates = mnemonics[:1]
    else:
        candidates = []
        if policy == "auto":
            candidates += mnemonics
            for m in mnemonics:
                initial = m[0].lower()
                if _alias_ok(initial) and initial not in used and initial not in candidates:
                    candidates.append(initial)
        if "" not in used:
            candidates.append("")
        letters = [c for c in _LETTERS if c not in used and c not in candidates][:4]
        candidates += letters
        if not letters:
            k = 0
            while f"p{k}" in used:
                k += 1
            candidates.append(f"p{k}")

    sample = owned if len(owned) <= 4000 else sorted(owned, key=lambda t: (-uses.total[t[0]], t[0]))[:4000]

    def cost(alias: str, items: List[Tuple[str, str]]) -> int:
        return tok.count(f"PREFIX {alias}:{_iri_ref(ns)}\n") + sum(uses.cost(tok, i, f"{alias}:{local}") for i, local in items)

    best = None
    for alias in candidates:
        c = cost(alias, sample)
        if best is None or c < best[0]:
            best = (c, alias)
    alias = best[1]
    if cost(alias, owned) >= sum(full[i] for i, _ in owned):
        return None   # the prefix does not pay for its declaration
    return alias


def _plan_dictionary(uses: _Uses, full: Dict[str, int], tok: TokenCounter, plan: _Plan) -> None:
    candidates = []
    for iri in uses.total:
        if iri in plan.text:
            continue
        net = full[iri] - (uses.cost(tok, iri, "I0") + tok.count(f"I0={_iri_ref(iri)}\n"))
        if net > 0:
            candidates.append((-net, iri))
    candidates.sort()
    chosen: List[Tuple[str, str]] = []
    saved = 0
    for _neg, iri in candidates:
        ix = f"I{len(chosen)}"
        net = full[iri] - (uses.cost(tok, iri, ix) + tok.count(f"{ix}={_iri_ref(iri)}\n"))
        if net > 0:
            chosen.append((ix, iri))
            saved += net
    if chosen and saved > tok.count("DICT\n"):
        for ix, iri in chosen:
            plan.text[iri] = ix
            plan.index[ix] = iri


# ------------------------- rendering -------------------------

class _Renderer:
    def __init__(self, an: _Analysis, plan: _Plan, opts: LoretoOptions, layout: str):
        self.an, self.plan, self.opts = an, plan, opts
        self.grouped = layout == "grouped"
        if self.grouped:
            labelled = [b for b in an.bnodes
                        if b not in an.inline and b not in an.list_nodes and b not in an.anonymous]
        else:
            labelled = list(an.bnodes)
        labelled.sort(key=lambda b: (an.colour.get(b, ""), an.first_seen[b]))
        self.labels = {b: f"_:b{i}" for i, b in enumerate(labelled)}
        self.aliases: Set[str] = set()
        self.indices: Set[str] = set()
        self.features: Set[str] = set()
        self.emitted = 0

    # --- terms
    def iri(self, iri: str) -> str:
        text = self.plan.text.get(iri)
        if text is None:
            return _iri_ref(iri)
        alias = self.plan.alias_of.get(iri)
        if alias is not None:
            self.aliases.add(alias)
        else:
            self.indices.add(text)
        return text

    def term(self, t, pos: str) -> str:
        if isinstance(t, URIRef):
            if pos == "p" and t == RDF.type:
                self.features.add("a")
                return "a"
            if pos == "o" and self.opts.lists and t == RDF.nil:
                self.features.add("nil")
                return "()"
            return self.iri(str(t))
        if isinstance(t, BNode):
            self.features.add("label")
            return self.labels[t]
        if isinstance(t, Literal):
            return self.literal(t)
        raise TypeError(f"unsupported RDF term {t!r}")

    def literal(self, lit: Literal) -> str:
        lex = str(lit)
        if lit.language:
            return f"{_json_str(lex)}@{lit.language}"
        if lit.datatype is None or str(lit.datatype) == XSD_STRING:
            return _json_str(lex)
        dt = str(lit.datatype)
        if self.opts.literal_shorthand and _shorthand(dt, lex):
            self.features.add("number")
            return lex
        return f"{_json_str(lex)}^^{self.iri(dt)}"

    # --- layouts
    def flat(self, records: List[QuadRecord]) -> List[str]:
        lines = []
        for g, s, p, o in records:
            line = f"{self.term(s, 's')} {self.term(p, 'p')} {self.term(o, 'o')}"
            if g is not None:
                self.features.add("graph")
                line = f"GRAPH {self.term(g, 'g')} {line}"
            lines.append(line)
        self.emitted += len(records)
        lines.sort()
        return lines

    def grouped_lines(self) -> List[str]:
        an = self.an
        by_graph: Dict = defaultdict(list)
        for g, s in an.out:
            by_graph[g].append(s)
        graph_text = {g: ("" if g is None else self.term(g, "g")) for g in by_graph}
        lines: List[str] = []
        for g in sorted(by_graph, key=lambda g: (g is not None, graph_text[g])):
            if g is not None:
                self.features.add("graph")
                lines.append(f"GRAPH {graph_text[g]}")
            blocks = []
            for s in by_graph[g]:
                if isinstance(s, BNode) and (s in an.inline or s in an.list_nodes):
                    continue   # written inside its parent
                if isinstance(s, BNode) and s in an.anonymous:
                    self.features.add("anon")
                    head = "[]"
                else:
                    head = self.term(s, "s")
                blocks.append((head, self.predicate_lines(g, s)))
            blocks.sort()
            for head, body in blocks:
                lines.append(head)
                lines.extend(body)
        return lines

    def _predicates(self, g, s):
        po = self.an.out[(g, s)]
        items = sorted(((self.term(p, "p"), p) for p in po), key=lambda t: (t[0] != "a", t[0]))
        return po, items

    def predicate_lines(self, g, s) -> List[str]:
        po, items = self._predicates(g, s)
        last = len(items) - 1
        return [f" {text} {self.objects(g, po[p])}" + (" ;" if i < last else "") for i, (text, p) in enumerate(items)]

    def objects(self, g, objs: List[ObjectTerm]) -> str:
        self.emitted += len(objs)
        return ", ".join(sorted(self.obj(g, o) for o in objs))

    def obj(self, g, o) -> str:
        if isinstance(o, BNode):
            if o in self.an.list_heads:
                return self.collection(g, o)
            if o in self.an.inline:
                return self.inline(g, o)
        return self.term(o, "o")

    def inline(self, g, b: BNode) -> str:
        self.features.add("inline")
        if (g, b) not in self.an.out:
            return "[]"
        po, items = self._predicates(g, b)
        return "[ " + " ; ".join(f"{text} {self.objects(g, po[p])}" for text, p in items) + " ]"

    def collection(self, g, head: BNode) -> str:
        self.features.add("list")
        items, n = [], head
        while n != RDF.nil:
            po = self.an.out[(g, n)]
            items.append(self.obj(g, po[RDF.first][0]))
            n = po[RDF.rest][0]
            self.emitted += 2
        return "( " + " ".join(items) + " )"

    # --- header
    def header(self) -> List[str]:
        lines = [f"PREFIX {a}:{_iri_ref(self.plan.prefixes[a])}" for a in sorted(self.aliases)]
        if self.indices:
            lines.append("DICT")
            for ix in sorted(self.indices, key=lambda x: int(x[1:])):
                lines.append(f"{ix}={_iri_ref(self.plan.index[ix])}")
        return lines

    def legend(self) -> str:
        f = self.features
        parts = ["# Loreto RDF graph."]
        if self.aliases:
            parts.append("PREFIX p:<ns> means p:x is <nsx>.")
        if self.indices:
            parts.append("DICT lines In=<iri> define In.")
        if self.grouped:
            parts.append('Each subject line is followed by " predicate object, object ;" lines.')
        else:
            parts.append("Each line is: subject predicate object.")
        if "graph" in f:
            parts.append("GRAPH g: named graph g.")
        if "a" in f:
            parts.append("a = rdf:type.")
        if f & {"inline", "anon"}:
            parts.append("[ ... ] and [] are blank nodes.")
        if "label" in f:
            parts.append("_:bN names a blank node.")
        if "list" in f:
            parts.append("( ... ) is an RDF list.")
        if "nil" in f:
            parts.append("() is rdf:nil.")
        if "number" in f:
            parts.append("Bare numbers and true/false are typed literals.")
        return " ".join(parts)


# ------------------------- serialisation -------------------------

_GENID_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/]+/\.well-known/genid/")


def _deskolemize(records: List[QuadRecord]) -> List[QuadRecord]:
    mapping: Dict[URIRef, BNode] = {}

    def conv(t):
        if isinstance(t, URIRef) and _GENID_RE.match(t):
            b = mapping.get(t)
            if b is None:
                b = mapping[t] = BNode()
            return b
        return t

    return list(dict.fromkeys((None if g is None else conv(g), conv(s), p, conv(o)) for g, s, p, o in records))


def serialize(data, options: Optional[LoretoOptions] = None, *, return_stats: bool = False,
              counter: Optional[TokenCounter] = None, **overrides):
    
    t_start = time.perf_counter()
    opts = options or LoretoOptions()
    if overrides:
        opts = replace(opts, **overrides)
    tok = counter or TokenCounter(opts.tokenizer)
    records, declared = _as_records(data)
    if opts.deskolemize:
        records = _deskolemize(records)

    t0 = time.perf_counter()
    an = _analyse(records, opts)
    t1 = time.perf_counter()

    layouts = ("flat", "grouped") if opts.layout == "auto" else (opts.layout,)
    texts: Dict[str, str] = {}
    renderers: Dict[str, _Renderer] = {}
    plan_ms = render_ms = 0.0
    for layout in layouts:
        ta = time.perf_counter()
        plan = _plan(_occurrences(records, an, layout, opts), opts, tok, declared)
        tb = time.perf_counter()
        r = _Renderer(an, plan, opts, layout)
        body = r.grouped_lines() if layout == "grouped" else r.flat(records)
        if r.emitted != len(records):   # never drop a triple silently
            raise RuntimeError(f"Loreto internal error: the {layout} layout wrote {r.emitted} of {len(records)} triples")
        lines = r.header() + body
        if opts.legend and lines:
            lines.insert(0, r.legend())
        texts[layout] = "\n".join(lines) + "\n" if lines else ""
        renderers[layout] = r
        plan_ms += (tb - ta) * 1000
        render_ms += (time.perf_counter() - tb) * 1000

    t2 = time.perf_counter()
    counts = {layout: tok.count(text) for layout, text in texts.items()}
    if len(layouts) == 1:
        chosen = layouts[0]
    else:   # on a tie the grouped layout wins: same cost, easier to read
        chosen = "flat" if counts["flat"] < counts["grouped"] else "grouped"
    text = texts[chosen]
    if not return_stats:
        return text

    t3 = time.perf_counter()
    r = renderers[chosen]
    grouped = chosen == "grouped"
    stats = LoretoStats(
        tokenizer=tok.name,
        layout=chosen,
        triples=len(records),
        named_graphs=len({g for g, _s, _p, _o in records if g is not None}),
        tokens={**counts, "chosen": counts[chosen]},
        bytes=len(text.encode("utf-8")),
        prefixes=len(r.aliases),
        dictionary_entries=len(r.indices),
        blank_nodes={
            "total": len(an.bnodes),
            "labelled": len(r.labels),
            "inline": len(an.inline) if grouped else 0,
            "list_nodes": len(an.list_nodes) if grouped else 0,
            "anonymous": len(an.anonymous) if grouped else 0,
        },
        timings_ms={
            "prepare": round((t0 - t_start) * 1000, 3),
            "analyse": round((t1 - t0) * 1000, 3),
            "plan": round(plan_ms, 3),
            "render": round(render_ms, 3),
            "count": round((t3 - t2) * 1000, 3),
            "total": round((t3 - t_start) * 1000, 3),
        },
    )
    return text, stats


# ------------------------- parsing -------------------------

class LoretoSyntaxError(ValueError):
    pass


_TOKEN_RE = re.compile(r"""
    (?P<ws>[ \t]+)
  | (?P<iri><[^<>\x00-\x20]*>)
  | (?P<str>"(?:[^"\\]|\\.)*")
    (?:@(?P<lang>[A-Za-z]+(?:-[A-Za-z0-9]+)*)
      |\^\^(?P<dt><[^<>\x00-\x20]*>|[A-Za-z][A-Za-z0-9_-]*:[\w.\-]+|:[\w.\-]+|I[0-9]+))?
  | (?P<bnode>_:[A-Za-z0-9_.\-]+|_b[0-9]+)
  | (?P<curie>(?:[A-Za-z][A-Za-z0-9_-]*)?:[\w.\-]+)
  | (?P<num>[+-]?(?:[0-9]+\.[0-9]*[eE][+-]?[0-9]+|\.[0-9]+[eE][+-]?[0-9]+|[0-9]+[eE][+-]?[0-9]+|[0-9]*\.[0-9]+|[0-9]+))
  | (?P<punct>\(\)|\[\]|[\[\]();,])
  | (?P<word>[A-Za-z][A-Za-z0-9_-]*)
""", re.X)
_TOKEN_KINDS = ("ws", "iri", "str", "bnode", "curie", "num", "punct", "word")
_PREFIX_LINE_RE = re.compile(r"PREFIX ([A-Za-z][A-Za-z0-9_-]*)?:\s*(<[^<>\x00-\x20]*>)\s*")
_DICT_LINE_RE = re.compile(r"(I[0-9]+)=(<[^<>\x00-\x20]*>)\s*")


@dataclass
class _Tok:
    kind: str
    text: str
    lang: Optional[str] = None
    dt: Optional[str] = None


def _lex(line: str) -> List[_Tok]:
    toks: List[_Tok] = []
    pos = 0
    while pos < len(line):
        m = _TOKEN_RE.match(line, pos)
        if not m:
            raise LoretoSyntaxError(f"unexpected character {line[pos]!r} at column {pos + 1}")
        pos = m.end()
        kind = next(k for k in _TOKEN_KINDS if m.group(k) is not None)
        if kind == "ws":
            continue
        toks.append(_Tok(kind, m.group(kind), m.group("lang"), m.group("dt")) if kind == "str" else _Tok(kind, m.group(kind)))
    return toks


class _Parser:
    def __init__(self):
        self.ds = Dataset()
        self.default = _default_graph(self.ds)
        self.prefixes: Dict[str, str] = {}
        self.index: Dict[str, str] = {}
        self.labels: Dict[str, BNode] = {}
        self.graphs: Dict = {}
        self.graph = None     # current named graph (grouped layout)
        self.subject = None   # current subject (grouped layout)

    def add(self, s, p, o, g) -> None:
        if g is None:
            self.default.add((s, p, o))
        else:
            target = self.graphs.get(g)
            if target is None:
                target = self.graphs[g] = self.ds.graph(g)
            target.add((s, p, o))

    def line(self, line: str) -> None:
        if line.startswith("PREFIX "):
            m = _PREFIX_LINE_RE.fullmatch(line)
            if not m:
                raise LoretoSyntaxError("malformed PREFIX line")
            self.prefixes[m.group(1) or ""] = _unescape_iri(m.group(2)[1:-1])
            return
        if line.strip() == "DICT":
            return
        m = _DICT_LINE_RE.fullmatch(line)
        if m:
            self.index[m.group(1)] = _unescape_iri(m.group(2)[1:-1])
            return
        toks = _lex(line)
        if not toks:
            return
        if line[0] in " \t":   # " predicate objects" line of the current block
            if self.subject is None:
                raise LoretoSyntaxError("predicate line without a subject line")
            i = self.predicate_objects(toks, 0, self.subject, self.graph, end=None)
            if i != len(toks):
                raise LoretoSyntaxError(f"unexpected {toks[i].text!r}")
            return
        g = None
        if toks[0].kind == "word" and toks[0].text == "GRAPH":
            if len(toks) < 2:
                raise LoretoSyntaxError("GRAPH without a graph name")
            if len(toks) == 2:   # grouped layout: the following blocks belong to this graph
                self.graph = self.node(toks[1])
                self.subject = None
                return
            g, toks = self.node(toks[1]), toks[2:]
            if len(toks) < 3:
                raise LoretoSyntaxError("expected GRAPH g subject predicate object")
        if len(toks) == 1:       # grouped layout: subject line
            self.subject = self.subject_term(toks[0])
            return
        s = self.subject_term(toks[0])   # flat layout: [GRAPH g] s p o
        p = self.verb(toks[1])
        o, i = self.object(toks, 2, g)
        if i != len(toks):
            raise LoretoSyntaxError(f"unexpected {toks[i].text!r}")
        self.add(s, p, o, g)

    def predicate_objects(self, toks: List[_Tok], i: int, subject, g, end: Optional[str]) -> int:
        while i < len(toks) and not (end and toks[i].kind == "punct" and toks[i].text == end):
            p = self.verb(toks[i])
            i += 1
            while True:
                o, i = self.object(toks, i, g)
                self.add(subject, p, o, g)
                if i < len(toks) and toks[i].kind == "punct" and toks[i].text == ",":
                    i += 1
                    continue
                break
            if i < len(toks) and toks[i].kind == "punct" and toks[i].text == ";":
                i += 1
                continue
            break
        return i

    def object(self, toks: List[_Tok], i: int, g):
        if i >= len(toks):
            raise LoretoSyntaxError("missing object")
        t = toks[i]
        if t.kind == "punct":
            if t.text == "[]":
                return BNode(), i + 1
            if t.text == "()":
                return RDF.nil, i + 1
            if t.text == "[":
                b = BNode()
                i = self.predicate_objects(toks, i + 1, b, g, end="]")
                if i >= len(toks) or toks[i].text != "]":
                    raise LoretoSyntaxError("unclosed '['")
                return b, i + 1
            if t.text == "(":
                items, i = [], i + 1
                while i < len(toks) and not (toks[i].kind == "punct" and toks[i].text == ")"):
                    item, i = self.object(toks, i, g)
                    items.append(item)
                if i >= len(toks):
                    raise LoretoSyntaxError("unclosed '('")
                return self.collection(items, g), i + 1
            raise LoretoSyntaxError(f"unexpected {t.text!r}")
        return self.simple(t), i + 1

    def collection(self, items: List, g):
        if not items:
            return RDF.nil
        nodes = [BNode() for _ in items]
        for k, (node, item) in enumerate(zip(nodes, items)):
            self.add(node, RDF.first, item, g)
            self.add(node, RDF.rest, nodes[k + 1] if k + 1 < len(nodes) else RDF.nil, g)
        return nodes[0]

    def iri(self, t: _Tok) -> URIRef:
        if t.kind == "iri":
            return URIRef(_unescape_iri(t.text[1:-1]))
        if t.kind == "curie":
            alias, local = t.text.split(":", 1)
            if alias not in self.prefixes:
                raise LoretoSyntaxError(f"undeclared prefix '{alias}:'")
            return URIRef(self.prefixes[alias] + local)
        if t.kind == "word" and _INDEX_RE.fullmatch(t.text):
            if t.text not in self.index:
                raise LoretoSyntaxError(f"undefined dictionary entry {t.text}")
            return URIRef(self.index[t.text])
        raise LoretoSyntaxError(f"expected an IRI, got {t.text!r}")

    def simple(self, t: _Tok):
        if t.kind == "bnode":
            b = self.labels.get(t.text)
            if b is None:
                b = self.labels[t.text] = BNode()
            return b
        if t.kind == "str":
            try:
                lex = json.loads(t.text)
            except json.JSONDecodeError as e:
                raise LoretoSyntaxError(f"bad string literal {t.text!r}: {e}") from None
            if t.lang:
                return Literal(lex, lang=t.lang, normalize=False)
            if t.dt:
                dt_tok = _lex(t.dt)[0]
                return Literal(lex, datatype=self.iri(dt_tok), normalize=False)
            return Literal(lex, normalize=False)
        if t.kind == "num":
            if _SHORTHAND[_XSD + "double"].fullmatch(t.text):
                dt = XSD.double
            elif "." in t.text:
                dt = XSD.decimal
            else:
                dt = XSD.integer
            return Literal(t.text, datatype=dt, normalize=False)
        if t.kind == "word":
            if t.text in ("true", "false"):
                return Literal(t.text, datatype=XSD.boolean, normalize=False)
            if t.text == "a":   # 'a' is read as rdf:type in any position
                return RDF.type
        return self.iri(t)

    def node(self, t: _Tok):
        term = self.simple(t)
        if not isinstance(term, (URIRef, BNode)):
            raise LoretoSyntaxError(f"expected an IRI or blank node, got {t.text!r}")
        return term

    def subject_term(self, t: _Tok):
        if t.kind == "punct" and t.text == "[]":
            return BNode()
        return self.node(t)

    def verb(self, t: _Tok) -> URIRef:
        if t.kind == "word" and t.text == "a":
            return RDF.type
        return self.iri(t)


def parse(text: str) -> Dataset:
    """Parse Loreto text (flat or grouped layout, with or without legend) into a Dataset."""
    p = _Parser()
    for n, line in enumerate(text.split("\n"), 1):   # never splitlines(): U+2028 may appear in literals
        if line.endswith("\r"):
            line = line[:-1]
        if not line.strip(" \t") or line.startswith("#"):
            continue
        try:
            p.line(line)
        except LoretoSyntaxError as e:
            raise LoretoSyntaxError(f"line {n}: {e}") from None
    return p.ds


# ------------------------- verification -------------------------

_QUAD = "urn:x-loreto:quad#"


def _normalise_term(t):
    if isinstance(t, Literal) and not t.language and t.datatype is not None and str(t.datatype) == XSD_STRING:
        return Literal(str(t))   # RDF 1.1: "x"^^xsd:string and "x" are the same literal
    return t


def _quads_as_graph(quads) -> Graph:
    graph = Graph()
    qs, qp, qo, qg = (URIRef(_QUAD + x) for x in "spog")
    for g, s, p, o in quads:
        if g is None:
            graph.add((s, p, o))
        else:
            q = BNode()
            graph.add((q, qs, s))
            graph.add((q, qp, p))
            graph.add((q, qo, o))
            graph.add((q, qg, g))
    return graph


def _key(t) -> str:
    return "" if t is None else t.n3()


def _bnode_clusters(quads: Iterable[QuadRecord]) -> List[List[QuadRecord]]:
    parent: Dict[BNode, BNode] = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    quads = list(quads)
    for q in quads:
        bs = [t for t in (q[0], q[1], q[3]) if isinstance(t, BNode)]
        for b in bs:
            parent.setdefault(b, b)
        for b in bs[1:]:
            ra, rb = find(bs[0]), find(b)
            if ra != rb:
                parent[rb] = ra
    clusters: Dict[BNode, List[QuadRecord]] = defaultdict(list)
    for q in quads:
        b = next(t for t in (q[0], q[1], q[3]) if isinstance(t, BNode))
        clusters[find(b)].append(q)
    return list(clusters.values())


def _cluster_key(quads: List[QuadRecord]) -> Tuple[str, bool]:
    bnodes = {t for q in quads for t in (q[0], q[1], q[3]) if isinstance(t, BNode)}
    in_degree: Counter = Counter()
    tree = True
    for g, s, _p, o in quads:
        if isinstance(g, BNode) or (isinstance(s, BNode) and s == o):
            tree = False
        if isinstance(s, BNode) and isinstance(o, BNode):
            in_degree[o] += 1
    roots = [b for b in bnodes if in_degree[b] == 0]
    if tree and len(roots) == 1 and all(in_degree[b] <= 1 for b in bnodes):
        # Canonical (Merkle) hash of a labelled tree, computed bottom-up without recursion.
        out: Dict[BNode, List] = defaultdict(list)
        refs: Dict[BNode, List] = defaultdict(list)
        for g, s, p, o in quads:
            if isinstance(s, BNode):
                out[s].append((g, p, o))
            else:
                refs[o].append((g, s, p))
        digest: Dict[BNode, str] = {}
        stack = [(roots[0], False)]
        while stack:
            b, ready = stack.pop()
            if not ready:
                stack.append((b, True))
                stack.extend((o, False) for _g, _p, o in out[b] if isinstance(o, BNode))
                continue
            items = sorted(
                [f"o\x1f{_key(g)}\x1f{p.n3()}\x1f{digest[o] if isinstance(o, BNode) else o.n3()}" for g, p, o in out[b]]
                + [f"i\x1f{_key(g)}\x1f{s.n3()}\x1f{p.n3()}" for g, s, p in refs[b]])
            digest[b] = hashlib.sha256("\x1e".join(items).encode("utf-8")).hexdigest()
        return "T" + digest[roots[0]], True
    colour = _bnode_colours(quads, bnodes)
    relabelled = sorted("\x1f".join(colour[t] if isinstance(t, BNode) else _key(t) for t in q) for q in quads)
    key = "W" + hashlib.sha256("\x1e".join(relabelled).encode("utf-8")).hexdigest()
    return key, len(set(colour.values())) == len(bnodes)


def same_dataset(a: Iterable[QuadRecord], b: Iterable[QuadRecord]) -> bool:
    
    qa = {(g, s, p, _normalise_term(o)) for g, s, p, o in a}
    qb = {(g, s, p, _normalise_term(o)) for g, s, p, o in b}
    if len(qa) != len(qb):
        return False

    def has_bnode(q):
        return any(isinstance(t, BNode) for t in q)

    if {q for q in qa if not has_bnode(q)} != {q for q in qb if not has_bnode(q)}:
        return False
    keyed: List[Counter] = [Counter(), Counter()]
    hard: List[Dict[str, List]] = [defaultdict(list), defaultdict(list)]
    for side, quads in enumerate((qa, qb)):
        for cluster in _bnode_clusters(q for q in quads if has_bnode(q)):
            key, complete = _cluster_key(cluster)
            if complete:
                keyed[side][key] += 1
            else:
                hard[side][key].append(cluster)
    if keyed[0] != keyed[1] or set(hard[0]) != set(hard[1]):
        return False
    for key, clusters in hard[0].items():
        remaining = [_quads_as_graph(c) for c in hard[1][key]]
        if len(remaining) != len(clusters):
            return False
        for cluster in clusters:
            graph = _quads_as_graph(cluster)
            match = next((k for k, other in enumerate(remaining) if isomorphic(graph, other)), None)
            if match is None:
                return False
            del remaining[match]
    return True


def verify(data, text: str, options: Optional[LoretoOptions] = None) -> bool:
    """True if ``text`` parses back to a dataset isomorphic to ``data``. With
    ``deskolemize`` the comparison is against the de-skolemised dataset."""
    records, _declared = _as_records(data)
    if options is not None and options.deskolemize:
        records = _deskolemize(records)
    return same_dataset(records, dataset_to_records(parse(text)))


# ------------------------- CLI -------------------------

def _arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="loreto.py",
        description="RDF/OWL -> Loreto, a token-efficient and lossless serialisation for LLM prompts. "
                    "Given a .loreto file (or --decode) it converts back to Turtle/TriG.")
    ap.add_argument("input", help="RDF/OWL file (.ttl/.owl/.rdf/.nt/.nq/.trig/.jsonld/...) or a .loreto file")
    ap.add_argument("-o", "--output", help="output path (default: <input>.loreto, or <input>.decoded.ttl/.trig)")
    ap.add_argument("--no-ttl", action="store_true", help="do not write <input>.normalized.ttl")
    g = ap.add_argument_group("cost function")
    g.add_argument("--tokenizer", default="o200k_base",
                   help="o200k_base (default), cl100k_base, any tiktoken encoding, tiktoken:<model>, "
                        "path/to/tokenizer.json (Hugging Face tokenizer file, offline), hf:<repo-id>, bytes, chars")
    g.add_argument("--no-tiktoken", action="store_true", help="deprecated: same as --tokenizer bytes")
    g = ap.add_argument_group("transformations (switch them off for ablations)")
    g.add_argument("--layout", choices=_LAYOUTS, default="auto", help="auto picks the cheaper layout")
    g.add_argument("--aliases", choices=_ALIAS_POLICIES, default="auto",
                   help="auto: cheapest alias, mnemonic on ties; short: letters; mnemonic: always owl:, foaf:, GO:...")
    g.add_argument("--no-prefixes", action="store_true", help="no PREFIX declarations")
    g.add_argument("--no-id-prefixes", action="store_true", help="no OBO identifier-space prefixes (GO:0008150)")
    g.add_argument("--no-dict", action="store_true", help="no DICT entries")
    g.add_argument("--no-lists", action="store_true", help="no ( ... ) list syntax")
    g.add_argument("--no-literal-shorthand", action="store_true", help="always quote typed literals")
    g.add_argument("--no-inline", action="store_true", help="label every blank node instead of nesting it")
    g.add_argument("--deskolemize", action="store_true",
                   help="turn .well-known/genid IRIs (e.g. BioPortal's) back into blank nodes")
    g.add_argument("--legend", action="store_true", help="prepend a '#' line describing the syntax used")
    g.add_argument("--max-prefixes", type=int, default=128, help="maximum number of prefixes (default: 128)")
    g = ap.add_argument_group("checks and statistics")
    g.add_argument("--verify", action="store_true",
                   help="parse the output back and check it is isomorphic to the input (exit code 2 if not)")
    g.add_argument("--stats-json", metavar="PATH", help="write statistics as JSON to PATH ('-' for stdout)")
    g.add_argument("--decode", action="store_true", help="read Loreto and write Turtle (or TriG with named graphs)")
    return ap


def _decode(in_path: Path, output: Optional[str]) -> int:
    ds = parse(in_path.read_text(encoding="utf-8"))
    records = dataset_to_records(ds)
    if any(g is not None for g, _s, _p, _o in records):
        data, ext = ds.serialize(format="trig"), ".trig"
    else:
        data, ext = records_to_union_graph(records).serialize(format="turtle"), ".ttl"
    out_path = Path(output) if output else in_path.with_name(in_path.stem + ".decoded" + ext)
    out_path.write_text(data, encoding="utf-8")
    print(f"[DECODE] {len(records)} triples -> {out_path}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = _arg_parser().parse_args(argv)
    in_path = Path(args.input)
    if not in_path.exists():
        raise SystemExit(f"[ERROR] Does not exist: {in_path}")
    if args.decode or in_path.suffix.lower() == ".loreto":
        try:
            return _decode(in_path, args.output)
        except LoretoSyntaxError as e:
            raise SystemExit(f"[ERROR] {e}")

    tokenizer = args.tokenizer
    if args.no_tiktoken:
        print("[WARN] --no-tiktoken is deprecated; use --tokenizer bytes", file=sys.stderr)
        tokenizer = "bytes"
    try:
        opts = LoretoOptions(
            tokenizer=tokenizer, layout=args.layout, aliases=args.aliases, prefixes=not args.no_prefixes,
            id_prefixes=not args.no_id_prefixes, dictionary=not args.no_dict, lists=not args.no_lists,
            literal_shorthand=not args.no_literal_shorthand, inline_bnodes=not args.no_inline,
            deskolemize=args.deskolemize, legend=args.legend, max_prefixes=args.max_prefixes)
        tok = TokenCounter(opts.tokenizer)
        t0 = time.perf_counter()
        ds = parse_to_dataset(in_path)
        parse_ms = (time.perf_counter() - t0) * 1000
        text, stats = serialize(ds, opts, return_stats=True, counter=tok)
    except (ImportError, RuntimeError, ValueError) as e:
        raise SystemExit(f"[ERROR] {e}")

    out_path = Path(args.output) if args.output else in_path.with_name(in_path.stem + ".loreto")
    out_path.write_text(text, encoding="utf-8")
    if not args.no_ttl:
        ttl = records_to_union_graph(dataset_to_records(ds)).serialize(format="turtle")
        in_path.with_name(in_path.stem + ".normalized.ttl").write_text(ttl, encoding="utf-8")

    raw = in_path.read_bytes()
    input_cost = tok.count(raw.decode("utf-8", errors="replace"))
    output_cost = stats.tokens["chosen"]
    ratio = output_cost / input_cost if input_cost > 0 else 0.0
    report = sys.stderr if args.stats_json == "-" else sys.stdout
    print(f"[STATS] tokenizer          : {tok.name} | layout: {stats.layout} | triples: {stats.triples}", file=report)
    print("[STATS] input_tokens/bytes :", input_cost, "/", len(raw), file=report)
    print("[STATS] output_tokens/bytes:", output_cost, "/", stats.bytes, file=report)
    print(f"[STATS] reduction_tokens   : {input_cost - output_cost} ({(1.0 - ratio) * 100.0:.2f}%)", file=report)
    print(f"[STATS] time               : parse {parse_ms:.1f} ms, serialise {stats.timings_ms['total']:.1f} ms", file=report)

    ok = True
    verify_ms = None
    if args.verify:
        tv = time.perf_counter()
        ok = verify(ds, text, opts)
        verify_ms = (time.perf_counter() - tv) * 1000
        print(f"[VERIFY] round-trip {'OK' if ok else 'FAILED'} ({verify_ms:.1f} ms)", file=report)

    if args.stats_json:
        payload = {"input": str(in_path), "input_tokens": input_cost, "input_bytes": len(raw),
                   "parse_ms": round(parse_ms, 3), "verified": ok if args.verify else None,
                   "verify_ms": None if verify_ms is None else round(verify_ms, 3), **stats.as_dict()}
        dump = json.dumps(payload, indent=2)
        if args.stats_json == "-":
            print(dump)
        else:
            Path(args.stats_json).write_text(dump + "\n", encoding="utf-8")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
