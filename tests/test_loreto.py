"""Tests for loreto.py: round-trip (lossless), determinism, aliases, tokenisers, CLI.

Run with:  python -m pytest tests -q
"""
from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

import pytest
from rdflib import BNode, Dataset, Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection
from rdflib.namespace import OWL, RDF, RDFS, XSD

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import loreto  # noqa: E402
from loreto import LoretoOptions, LoretoSyntaxError, TokenCounter, parse, serialize, verify  # noqa: E402

EX = Namespace("http://example.org/")
PEOPLE = Namespace("http://example.org/people/")
OBO = Namespace("http://purl.obolibrary.org/obo/")

OPTION_SETS = [
    {},
    {"layout": "flat"},
    {"layout": "grouped"},
    {"prefixes": False},
    {"id_prefixes": False},
    {"dictionary": False},
    {"aliases": "short"},
    {"aliases": "mnemonic"},
    {"lists": False},
    {"inline_bnodes": False},
    {"literal_shorthand": False},
    {"legend": True},
    {"layout": "flat", "prefixes": False, "dictionary": False, "lists": False,
     "inline_bnodes": False, "literal_shorthand": False},
]


def roundtrip(data, **options) -> str:
    opts = LoretoOptions(**options)
    text = serialize(data, opts)
    assert verify(data, text, opts), f"round-trip failed with {options}:\n{text}"
    return text


def prefix_lines(text: str):
    return dict(re.findall(r"^PREFIX ([^:]*):<([^>]*)>$", text, flags=re.M))


# ---------------------------------------------------------------- lists and blank nodes

def test_long_list_is_complete_and_uses_list_syntax():
    g = Graph()
    head = BNode()
    Collection(g, head, [EX[f"C{i}"] for i in range(15)])
    g.add((EX.U, OWL.unionOf, head))
    text = roundtrip(g)
    assert "( " + " ".join(f":C{i}" for i in range(15)) + " )" in text


def test_nested_lists_with_blank_node_items():
    g = Graph()
    inner, outer, restriction = BNode(), BNode(), BNode()
    g.add((restriction, RDF.type, OWL.Restriction))
    g.add((restriction, OWL.onProperty, EX.p))
    g.add((restriction, OWL.someValuesFrom, EX.D))
    Collection(g, inner, [EX.A, EX.B])
    Collection(g, outer, [inner, restriction, EX.C])
    g.add((EX.X, OWL.intersectionOf, outer))
    text = roundtrip(g)
    assert "( ( " in text and "[ a " in text


def test_blank_node_cycle_is_kept():
    b1, b2 = BNode(), BNode()
    data = [(b1, EX.p, b2), (b2, EX.p, b1), (EX.s, EX.q, EX.o)]
    for options in OPTION_SETS:
        roundtrip(data, **options)


def test_blank_node_self_loop_is_kept():
    b = BNode()
    roundtrip([(b, EX.p, b), (EX.s, EX.q, b)])


def test_blank_node_shared_between_named_graphs():
    b = BNode()
    data = [(EX.g1, EX.s, EX.p, b), (EX.g2, b, EX.q, Literal("x")), (EX.g2, EX.t, EX.p, EX.o)]
    for options in OPTION_SETS:
        text = roundtrip(data, **options)
        assert "[]" not in text   # it must be a label, not a fresh blank node


def test_identical_blank_nodes_are_not_merged():
    c1, c2 = BNode(), BNode()
    data = [(EX.C, RDFS.subClassOf, c1), (c1, OWL.onProperty, EX.P),
            (EX.C, RDFS.subClassOf, c2), (c2, OWL.onProperty, EX.P)]
    text = roundtrip(data, layout="grouped")
    assert text.count("[ ") == 2


def test_deep_nesting_does_not_recurse_forever():
    data, node = [], EX.root
    for _ in range(120):
        child = BNode()
        data.append((node, EX.next, child))
        node = child
    data.append((node, EX.value, Literal(1)))
    for options in OPTION_SETS:
        roundtrip(data, **options)


def test_rdf_type_only_abbreviated_as_predicate():
    data = [(EX.x, RDF.type, EX.C), (EX.P, OWL.equivalentProperty, RDF.type), (RDF.type, RDFS.label, Literal("type"))]
    text = roundtrip(data)
    for line in text.splitlines():
        tokens = line.split()
        if "a" in tokens:
            assert tokens.index("a") == (1 if not line.startswith(" ") else 0), line


def test_alias_a_is_never_used_and_aliases_are_valid():
    g = Graph()
    for k in range(60):   # more namespaces than letters
        ns = Namespace(f"http://example.org/ns{k}/")
        for i in range(5):
            g.add((ns[f"s{i}"], ns.p, ns[f"o{i}"]))
    g.add((EX.x, RDF.type, OWL.Class))
    text = roundtrip(g)
    aliases = prefix_lines(text)
    assert "a" not in aliases
    for alias in aliases:
        assert alias == "" or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", alias)
        assert alias not in {"_", "true", "false", "I"} and not re.fullmatch(r"I[0-9]+", alias)


def test_conventional_prefixes_when_they_cost_nothing():
    g = Graph()
    for i in range(10):
        g.add((EX[f"C{i}"], RDF.type, OWL.Class))
        g.add((EX[f"C{i}"], RDFS.subClassOf, EX.Top))
    aliases = prefix_lines(roundtrip(g))
    assert aliases.get("owl") == str(OWL)


def test_obo_identifier_space_prefixes():
    g = Graph()
    for i in range(40):
        g.add((OBO[f"GO_{i:07d}"], RDFS.subClassOf, OBO[f"GO_{i + 1:07d}"]))
    g.add((EX.x, RDFS.label, Literal("the most frequent namespace takes the empty alias")))
    for i in range(40):
        g.add((EX[f"t{i}"], RDFS.seeAlso, EX[f"u{i}"]))
    text = roundtrip(g)
    assert prefix_lines(text).get("GO") == str(OBO) + "GO_"
    assert "GO:0000001" in text
    assert "GO:" not in roundtrip(g, id_prefixes=False)
    assert "GO:0000001" in roundtrip(g, aliases="mnemonic")


def test_mnemonic_policy_uses_names():
    g = Graph()
    for i in range(5):
        g.add((PEOPLE[f"p{i}"], RDF.type, URIRef("http://xmlns.com/foaf/0.1/Person")))
        g.add((PEOPLE[f"p{i}"], URIRef("http://xmlns.com/foaf/0.1/knows"), PEOPLE[f"p{(i + 1) % 5}"]))
    aliases = prefix_lines(roundtrip(g, aliases="mnemonic"))
    assert aliases.get("foaf") == "http://xmlns.com/foaf/0.1/"
    assert aliases.get("people") == str(PEOPLE)


# ---------------------------------------------------------------- literals and IRIs

LITERALS = [
    Literal("Málaga, España — Ελλάδα", lang="es"),
    Literal("line1\nline2\ttab \"quoted\" back\\slash"),
    Literal("separator inside and\x85next"),
    Literal(""),
    Literal("x", datatype=XSD.string),
    Literal(42), Literal(-7), Literal("01", datatype=XSD.integer, normalize=False),
    Literal("4.25", datatype=XSD.decimal), Literal("1.5e3", datatype=XSD.double, normalize=False),
    Literal("1000.0", datatype=XSD.double), Literal("INF", datatype=XSD.double, normalize=False),
    Literal(True), Literal("abc", datatype=XSD.integer),
    Literal("2020-01-01", datatype=XSD.date), Literal("x", datatype=EX.custom),
    Literal("en-GB text", lang="en-GB"),
]


@pytest.mark.parametrize("options", OPTION_SETS, ids=str)
def test_literals_roundtrip(options):
    data = [(EX.s, EX[f"p{i}"], lit) for i, lit in enumerate(LITERALS)]
    roundtrip(data, **options)


def test_literal_forms():
    text = serialize([(EX.s, EX.p, Literal("Málaga")), (EX.s, EX.q, Literal(42)),
                      (EX.s, EX.r, Literal("1000.0", datatype=XSD.double))])
    assert '"Málaga"' in text                          # no \u escapes
    assert re.search(r" \S*:q 42( ;)?$", text, flags=re.M)  # bare integer
    assert '"1000.0"^^' in text                     # no exponent: a bare 1000.0 would be a decimal


def test_unsafe_local_names_and_strange_iris():
    data = [(URIRef("http://example.org/a(b)"), EX.p, URIRef("http://example.org/100%25")),
            (URIRef("http://example.org/with space"), EX.p, URIRef("http://example.org/x>y")),
            (URIRef("urn:isbn:0451450523"), EX.p, URIRef("http://example.org/ñandú")),
            (URIRef("http://example.org/"), EX.p, URIRef("http://example.org/a#")),
            (URIRef("http://example.org/a:b"), EX.p, Literal("x"))]
    for options in OPTION_SETS:
        roundtrip(data, **options)
    assert ":ñandú" in serialize(data)


def test_named_graph_with_blank_node_name():
    gname = BNode()
    data = [(gname, EX.s, EX.p, EX.o), (None, gname, RDFS.label, Literal("a graph"))]
    for options in OPTION_SETS:
        roundtrip(data, **options)


def test_dataset_input():
    ds = Dataset()
    ds.add((EX.a, EX.p, EX.b))
    ds.graph(EX.g).add((EX.a, EX.p, Literal("in g")))
    text = roundtrip(ds)
    assert re.search(r"^GRAPH \S*:g\b", text, flags=re.M)


def test_empty_input():
    assert serialize(Graph()) == ""
    assert verify(Graph(), "")


# ---------------------------------------------------------------- determinism

def random_dataset(seed: int):
    rnd = random.Random(seed)
    namespaces = [Namespace(f"http://example.org/v{k}/") for k in range(4)] + [OBO]

    def iri():
        ns = rnd.choice(namespaces)
        return ns[f"GO_{rnd.randint(0, 50):07d}"] if ns == OBO else ns[f"t{rnd.randint(0, 30)}"]

    bnodes = [BNode() for _ in range(12)]
    graphs = [None, None, None, EX.g1, EX.g2, bnodes[0]]
    quads = []
    for _ in range(80):
        g = rnd.choice(graphs)
        s = rnd.choice([iri(), iri(), rnd.choice(bnodes)])
        p = RDF.type if rnd.random() < 0.15 else iri()
        o = rnd.choice([iri(), rnd.choice(bnodes), Literal(rnd.randint(-5, 500)),
                        Literal(f"text {rnd.randint(0, 9)}", lang=rnd.choice(["en", "es"])), Literal("plain")])
        quads.append((g, s, p, o))
    for _ in range(3):   # lists, sometimes malformed
        g = rnd.choice(graphs[:4])
        nodes = [BNode() for _ in range(rnd.randint(1, 14))]
        for k, n in enumerate(nodes):
            quads.append((g, n, RDF.first, iri() if rnd.random() < 0.7 else Literal(k)))
            quads.append((g, n, RDF.rest, nodes[k + 1] if k + 1 < len(nodes) else RDF.nil))
        if rnd.random() < 0.3:
            quads.append((g, rnd.choice(nodes), EX.extra, Literal("breaks the list")))
        quads.append((g, iri(), EX.members, nodes[0]))
    return list(dict.fromkeys(quads))


@pytest.mark.parametrize("seed", range(12))
def test_random_datasets_roundtrip(seed):
    data = random_dataset(seed)
    for options in OPTION_SETS:
        roundtrip(data, **options)


@pytest.mark.parametrize("seed", range(6))
def test_output_does_not_depend_on_input_order(seed):
    data = random_dataset(seed)
    for options in ({}, {"layout": "flat"}, {"inline_bnodes": False}):
        outputs = set()
        for k in range(4):
            shuffled = data[:]
            random.Random(k).shuffle(shuffled)
            outputs.add(serialize(shuffled, LoretoOptions(**options)))
        assert len(outputs) == 1, options


def test_output_does_not_depend_on_blank_node_ids():
    def build():
        b1, b2 = BNode(), BNode()
        return [(EX.s, EX.p, b1), (EX.t, EX.p, b1), (b1, EX.q, b2), (EX.u, EX.p, b2), (b2, EX.q, EX.v)]
    assert serialize(build()) == serialize(build())


# ---------------------------------------------------------------- tokenisers

def test_missing_tiktoken_is_an_error_not_a_fallback(monkeypatch):
    monkeypatch.setitem(sys.modules, "tiktoken", None)
    with pytest.raises(ImportError):
        TokenCounter("o200k_base")
    assert TokenCounter("bytes").count("Málaga") == 7


def test_unknown_encoding_is_an_error():
    with pytest.raises(ValueError):
        TokenCounter("no_such_encoding")


def test_tokenizer_changes_the_cost_function():
    g = Graph()
    for i in range(20):
        g.add((OBO[f"GO_{i:07d}"], RDFS.label, Literal(f"term {i}")))
    _, st1 = serialize(g, return_stats=True)
    _, st2 = serialize(g, tokenizer="cl100k_base", return_stats=True)
    assert st1.tokenizer == "o200k_base" and st2.tokenizer == "cl100k_base"
    roundtrip(g, tokenizer="bytes")


def test_tokenizer_json_file(tmp_path):
    """A local tokenizer.json (Hugging Face format) is used as the cost function, offline."""
    pytest.importorskip("tokenizers")
    from tokenizers import Tokenizer, models, pre_tokenizers
    tk = Tokenizer(models.WordLevel({"[UNK]": 0, "hello": 1}, unk_token="[UNK]"))
    tk.pre_tokenizer = pre_tokenizers.Whitespace()
    path = tmp_path / "tokenizer.json"
    tk.save(str(path))

    assert TokenCounter(str(path)).count("hello big world") == 3
    assert TokenCounter("hf-file:" + str(path)).count("hello") == 1
    with pytest.raises(FileNotFoundError):
        TokenCounter(str(tmp_path / "missing.json"))

    g = Graph()
    g.add((EX.a, RDF.type, OWL.Class))
    g.add((EX.a, RDFS.label, Literal("A")))
    text, st = serialize(g, tokenizer=str(path), return_stats=True)
    assert st.tokenizer == str(path) and verify(g, text)

    src = tmp_path / "onto.ttl"
    g.serialize(src, format="turtle")
    assert loreto.main([str(src), "--tokenizer", str(path), "--no-ttl", "--verify"]) == 0


# ---------------------------------------------------------------- options, legend, stats

def test_deskolemize():
    genid = "http://bioportal.bioontology.org/.well-known/genid/"
    r1 = URIRef(genid + "abc123")
    data = [(EX.C, RDFS.subClassOf, r1), (r1, RDF.type, OWL.Restriction), (r1, OWL.onProperty, EX.p)]
    opts = LoretoOptions(deskolemize=True)
    text = serialize(data, opts)
    assert "genid" not in text
    assert re.search(r"\[ a \S*:Restriction", text)
    assert verify(data, text, opts)
    assert not verify(data, text)   # the genid IRI itself is gone, by design
    roundtrip(data)                 # without the option it is kept


def test_legend_is_a_comment_and_describes_what_is_used():
    g = Graph()
    head = BNode()
    Collection(g, head, [EX.A, EX.B])
    g.add((EX.x, OWL.unionOf, head))
    g.add((EX.x, RDF.type, OWL.Class))
    text = roundtrip(g, legend=True)
    first = text.splitlines()[0]
    assert first.startswith("# ") and "RDF list" in first and "rdf:type" in first
    assert "GRAPH" not in first


def test_stats():
    g = Graph()
    g.add((EX.a, EX.p, EX.b))
    text, st = serialize(g, return_stats=True)
    assert st.tokens["chosen"] == TokenCounter().count(text)
    assert st.triples == 1 and st.layout in ("flat", "grouped")
    assert set(st.timings_ms) >= {"analyse", "plan", "render", "total"}
    json.dumps(st.as_dict())


def test_invalid_options():
    with pytest.raises(ValueError):
        LoretoOptions(layout="columns")
    with pytest.raises(ValueError):
        LoretoOptions(aliases="random")


# ---------------------------------------------------------------- dataset comparison

def rename_bnodes(quads):
    mapping = {}

    def r(t):
        if isinstance(t, BNode):
            return mapping.setdefault(t, BNode())
        return t
    return [(r(g), r(s), p, r(o)) for g, s, p, o in quads]


def test_same_dataset_many_indistinguishable_blank_nodes():
    quads = []
    for i in range(3000):   # OBO-style axiom annotations, many of them identical
        b = BNode()
        quads += [(None, b, RDF.type, OWL.Axiom), (None, b, OWL.annotatedSource, EX[f"s{i % 7}"]),
                  (None, b, OWL.annotatedProperty, RDFS.label)]
    renamed = rename_bnodes(quads)
    assert loreto.same_dataset(quads, renamed)
    changed = renamed[:-1] + [(None, renamed[-1][1], OWL.annotatedProperty, RDFS.comment)]
    assert not loreto.same_dataset(quads, changed)


def test_same_dataset_cycles_and_shapes():
    b = [BNode() for _ in range(4)]
    four_cycle = [(None, b[k], EX.p, b[(k + 1) % 4]) for k in range(4)]
    c = [BNode() for _ in range(4)]
    two_two_cycles = [(None, c[0], EX.p, c[1]), (None, c[1], EX.p, c[0]),
                      (None, c[2], EX.p, c[3]), (None, c[3], EX.p, c[2])]
    assert loreto.same_dataset(four_cycle, rename_bnodes(four_cycle))
    assert not loreto.same_dataset(four_cycle, two_two_cycles)
    g1, g2 = BNode(), BNode()
    named = [(g1, EX.s, EX.p, g2), (g2, g1, EX.p, EX.o)]
    assert loreto.same_dataset(named, rename_bnodes(named))
    assert not loreto.same_dataset(named, [(g1, EX.s, EX.p, g2), (g2, g2, EX.p, EX.o)])


# ---------------------------------------------------------------- parser

def test_parse_errors_have_line_numbers():
    with pytest.raises(LoretoSyntaxError, match="line 2"):
        parse("PREFIX :<http://example.org/>\n:a nope:b :c\n")
    with pytest.raises(LoretoSyntaxError):
        parse(" :p :o\n")          # predicate line without subject
    with pytest.raises(LoretoSyntaxError):
        parse(":a :b I7\n")        # undeclared prefix / dictionary entry
    with pytest.raises(LoretoSyntaxError):
        parse("GRAPH <http://example.org/g> <http://example.org/s>\n")


def test_examples_roundtrip():
    sources = sorted(p for p in (ROOT / "examples").rglob("*") if p.suffix in {".ttl", ".rdf", ".nt"})
    assert sources
    for path in sources:
        ds = loreto.parse_to_dataset(path)
        for options in OPTION_SETS:
            roundtrip(ds, **options)


# ---------------------------------------------------------------- CLI

def test_cli_roundtrip(tmp_path):
    src = tmp_path / "onto.ttl"
    src.write_text("@prefix ex: <http://example.org/> .\n"
                   "ex:a a ex:C ; ex:n 3 ; ex:l ( ex:x ex:y ) .\n", encoding="utf-8")
    stats = tmp_path / "stats.json"
    assert loreto.main([str(src), "--verify", "--no-ttl", "--stats-json", str(stats)]) == 0
    out = tmp_path / "onto.loreto"
    assert out.exists() and json.loads(stats.read_text())["verified"] is True
    assert loreto.main([str(out)]) == 0   # .loreto input -> decode
    decoded = Graph().parse(tmp_path / "onto.decoded.ttl")
    assert len(decoded) == len(Graph().parse(src))
