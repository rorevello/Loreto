# The Loreto format

Loreto is a line-oriented text serialisation of RDF datasets, designed to be placed in LLM
prompts. It borrows its notation from Turtle and SPARQL (`PREFIX`, `a`, `[ ]`, `( )`, `_:b0`,
bare numbers), so a model that has seen Turtle can read it. Every representation choice is
made by measuring the token cost of the result with the tokeniser of the target model.

## Example

Turtle / TriG input:

```turtle
@prefix ex: <http://example.org/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:Researcher rdfs:subClassOf [ a owl:Restriction ; owl:onProperty ex:worksAt ;
                                owl:someValuesFrom ex:Organisation ] .
ex:alice a ex:Researcher ; ex:age 42 ; ex:name "Alicia Pérez"@es ;
         ex:colleagues ( ex:bob ex:carol ) .
ex:provenance {
  _:src ex:retrieved "2026-09-01"^^xsd:date ; ex:reliable true .
  ex:alice ex:source _:src .
  ex:bob ex:source _:src .
}
```

Loreto, grouped layout, with the optional legend line (`serialize(ds, layout="grouped", legend=True)`):

```text
# Loreto RDF graph. PREFIX p:<ns> means p:x is <nsx>. Each subject line is followed by " predicate object, object ;" lines. GRAPH g: named graph g. a = rdf:type. [ ... ] and [] are blank nodes. _:bN names a blank node. ( ... ) is an RDF list. Bare numbers and true/false are typed literals.
PREFIX :<http://example.org/>
PREFIX owl:<http://www.w3.org/2002/07/owl#>
:Researcher
 <http://www.w3.org/2000/01/rdf-schema#subClassOf> [ a owl:Restriction ; owl:onProperty :worksAt ; owl:someValuesFrom :Organisation ]
:alice
 a :Researcher ;
 :age 42 ;
 :colleagues ( :bob :carol ) ;
 :name "Alicia Pérez"@es
GRAPH :provenance
:alice
 :source _:b0
:bob
 :source _:b0
_:b0
 :reliable true ;
 :retrieved "2026-09-01"^^<http://www.w3.org/2001/XMLSchema#date>
```

`rdfs:` and `xsd:` are written in full because each is used once: declaring a prefix would
cost more tokens than it saves. The same dataset in the flat layout:

```text
PREFIX :<http://example.org/>
PREFIX owl:<http://www.w3.org/2002/07/owl#>
PREFIX rdf:<http://www.w3.org/1999/02/22-rdf-syntax-ns#>
:Researcher <http://www.w3.org/2000/01/rdf-schema#subClassOf> _:b2
:alice :age 42
:alice :colleagues _:b3
:alice :name "Alicia Pérez"@es
:alice a :Researcher
GRAPH :provenance :alice :source _:b0
GRAPH :provenance :bob :source _:b0
GRAPH :provenance _:b0 :reliable true
GRAPH :provenance _:b0 :retrieved "2026-09-01"^^<http://www.w3.org/2001/XMLSchema#date>
_:b1 rdf:first :carol
_:b1 rdf:rest ()
_:b2 a owl:Restriction
_:b2 owl:onProperty :worksAt
_:b2 owl:someValuesFrom :Organisation
_:b3 rdf:first :bob
_:b3 rdf:rest _:b1
```

More examples, with their inputs, are in [`examples/`](../examples).

## Grammar

A document is a sequence of lines separated by `\n` (a trailing `\r` is ignored). Tokens on
a line are separated by single spaces.

```ebnf
document       ::= (line "\n")*
line           ::= comment | prefix | "DICT" | entry | graphHeader
                 | subjectLine | predicateLine | flatLine | ""
comment        ::= "#" [^\n]*
prefix         ::= "PREFIX " ALIAS? ":" IRIREF
entry          ::= INDEX "=" IRIREF

(* grouped layout *)
graphHeader    ::= "GRAPH " node                  (* the following blocks are in graph node *)
subjectLine    ::= node | "[]"                    (* no leading space *)
predicateLine  ::= " " verb " " objectList (" ;")?

(* flat layout *)
flatLine       ::= ("GRAPH " node " ")? node " " verb " " object

verb           ::= "a" | iri
objectList     ::= object (", " object)*
object         ::= iri | BNODE | literal | "[]" | "()"
                 | "[ " propertyList " ]" | "( " (object " ")* ")"
propertyList   ::= verb " " objectList (" ; " verb " " objectList)*
node           ::= iri | BNODE
iri            ::= IRIREF | CURIE | INDEX
literal        ::= STRING ("@" LANGTAG | "^^" iri)? | NUMBER | "true" | "false"

IRIREF         ::= "<" ([^<>\x00-\x20] | UCHAR)* ">"
CURIE          ::= ALIAS? ":" LOCAL
ALIAS          ::= [A-Za-z][A-Za-z0-9_-]*
LOCAL          ::= [\w.-]+                        (* Unicode letters and digits, "_", ".", "-" *)
INDEX          ::= "I" [0-9]+
BNODE          ::= "_:b" [0-9]+
STRING         ::= a JSON string (RFC 8259), non-ASCII characters written as themselves
LANGTAG        ::= [A-Za-z]+ ("-" [A-Za-z0-9]+)*
NUMBER         ::= Turtle INTEGER | DECIMAL | DOUBLE
UCHAR          ::= "\u" HEX{4} | "\U" HEX{8}
```

The writer never uses `a`, `_`, `true`, `false`, `I`, `I<n>`, `PREFIX`, `DICT` or `GRAPH` as an
alias.

## Semantics

| Text | RDF term |
|---|---|
| `p:x` | the IRI `ns + x`, where `PREFIX p:<ns>` is declared in the header |
| `I7` | the IRI declared as `I7=<…>` after `DICT` |
| `a` (predicate) | `rdf:type` |
| `_:b3` | the same blank node everywhere in the document |
| `[]` | a new blank node |
| `[ p o ; q r ]` | a new blank node with those properties |
| `( x y )` | an RDF list: new blank nodes linked by `rdf:first` / `rdf:rest`, ending in `rdf:nil` |
| `()` | `rdf:nil` |
| `"…"` | a simple literal (`xsd:string`) |
| `"…"@es`, `"…"^^iri` | a language-tagged or typed literal; the lexical form is the JSON string |
| `42`, `-3.5`, `1.2e3` | `xsd:integer`, `xsd:decimal`, `xsd:double` (Turtle rules), same lexical form |
| `true`, `false` | `xsd:boolean` |
| `GRAPH g` | the triples of the following blocks (grouped) or of that line (flat) are in named graph `g` |

## Guarantees

**Lossless.** `parse(serialize(D))` is isomorphic to `D` as an RDF dataset: the same quads up
to a consistent renaming of blank nodes, which may be shared between named graphs. Two
things are not distinguished: `"x"` and `"x"^^xsd:string`, which are the same literal in RDF
1.1. `verify(D, text)` performs this check. The test suite runs it on the examples and on
random datasets under every combination of options. `scripts/benchmark_corpus.py --verify`
runs it on a whole corpus.

**Deterministic.** The text is a function of the dataset. It does not depend on the order of
the input triples or on blank node identifiers:
- every list of lines, predicates and objects is sorted;
- ties in the planning are broken by the IRI text;
- labelled blank nodes are numbered by a canonical colour (colour refinement, i.e.
  1-dimensional Weisfeiler–Leman, over the dataset).

The only exception is labelled blank nodes that colour refinement cannot tell apart and that
are not symmetric. They are numbered in input order, which can change the text but never its
meaning.

**Not preserved.** Loreto keeps the RDF dataset, not the source file. It drops comments,
formatting, the source's prefix names and empty named graphs. With `deskolemize`,
`/.well-known/genid/` IRIs become blank nodes; `verify` then compares against the
de-skolemised dataset.

**Never silently wrong.** The writer counts the triples it emits and raises an error if the
count differs from the input. A missing tokeniser backend is an error, not a fallback to
counting bytes.

## How the text is chosen

1. **Normalise** the input to unique quads `(g, s, p, o)`.
2. **Blank nodes.**
   - A blank node is nested as `[ … ]` inside its parent only if all of these hold: it appears
     in a single graph, it has exactly one incoming edge, it does not name a graph, it is not
     on a cycle, and it is at most 48 levels deep.
   - Well-formed RDF lists become `( … )`.
   - Blank nodes that are never objects become `[]` subjects.
   - All the others get a canonical label `_:bN`.
3. **Count** how often each IRI would be written in each layout, and where.
   - `rdf:type` as a predicate, `rdf:nil`, list links and shorthand datatypes are not written,
     so they are not counted. Datatype IRIs are counted.
   - Subjects start a line; every other term follows a space. Tokenisers split the two cases
     differently: in `o200k_base`, `aura:Rule` costs 4 tokens at the start of a line and 3
     after a space, while `b:Rule` costs 3 in both. Every cost below is therefore measured
     for the position where the term is written.
4. **Prefixes.**
   - Candidate namespaces: every IRI is split after its last `#`, after its last `/`, and for
     OBO-style identifiers such as `…/obo/GO_0008150` after the identifier space (`…/obo/GO_`).
     A candidate needs a local name that is valid in a CURIE.
   - Namespaces are selected greedily by marginal token gain, net of the `PREFIX` line. The
     search is lazy, re-evaluating only the top of the heap.
5. **Aliases**, most frequent namespace first. The candidates are:
   - the conventional prefix (`owl`, `rdfs`, `foaf`, `skos`, `obo`…);
   - the prefix declared in the source;
   - a name derived from the IRI (`…/foaf/0.1/` → `foaf`, `…/obo/GO_` → `GO`);
   - its initial;
   - the empty alias;
   - letters.

   `aliases="auto"` takes the cheapest candidate, and on a tie the first in that order: a
   meaningful alias is used whenever it costs nothing extra. `short` uses only letters and
   `mnemonic` always uses the name. A prefix that does not pay for its declaration is dropped.
6. **Dictionary.** IRIs that still have no CURIE and repeat often enough get an entry
   `I<n>=<iri>`. IRIs that have a CURIE keep it, so their local names stay readable.
7. **Render** both layouts and keep the one with fewer tokens (grouped on a tie).

Most of the running time goes to counting tokens of short fragments and to sorting. The
counts are cached. The prefix search re-evaluates a namespace only when it reaches the top of
the heap, and it evaluates a few candidate aliases per namespace. Colour refinement is
`O(rounds × edges)` and only concerns blank nodes. `serialize(..., return_stats=True)` reports
the time of each phase, and `scripts/benchmark_corpus.py` reports it per ontology.

## Options

| Option (`LoretoOptions`) | CLI | Effect when switched off / changed |
|---|---|---|
| `tokenizer="o200k_base"` | `--tokenizer` | cost function: any tiktoken encoding, a path to a Hugging Face `tokenizer.json`, `hf:<repo>`, `bytes`, `chars` |
| `layout="auto"` | `--layout flat\|grouped` | force a layout |
| `prefixes=True` | `--no-prefixes` | no `PREFIX` lines (IRIs in full or in the dictionary) |
| `id_prefixes=True` | `--no-id-prefixes` | no OBO identifier-space prefixes |
| `aliases="auto"` | `--aliases short\|mnemonic` | alias policy |
| `dictionary=True` | `--no-dict` | no `DICT` |
| `lists=True` | `--no-lists` | lists as nested `rdf:first`/`rdf:rest` nodes, `rdf:nil` as an IRI |
| `literal_shorthand=True` | `--no-literal-shorthand` | numbers and booleans quoted with `^^` |
| `inline_bnodes=True` | `--no-inline` | every blank node labelled, no `[ … ]`, `[]` or lists |
| `deskolemize=False` | `--deskolemize` | skolem IRIs (`/.well-known/genid/`) back to blank nodes |
| `legend=False` | `--legend` | first line: a `#` comment describing the syntax used |
| `max_prefixes=128` | `--max-prefixes` | cap on the number of prefixes |
