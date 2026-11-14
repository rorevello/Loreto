# Examples

This directory contains small, concrete Loreto examples meant to complement the technical specification in [`docs/format.md`](../docs/format.md).

The goal of these examples is to show what Loreto looks like in practice for different kinds of RDF/OWL content.

The `.loreto` files in this directory are the converter's actual output (`o200k_base`, default
options), and each one parses back to its input (`python3 loreto.py <input> --verify`). To
regenerate one:

```bash
python3 loreto.py examples/owl/ontology.ttl -o examples/owl/ontology.loreto --no-ttl --verify
```

Important note:

- Loreto is optimised for tokens, not bytes. It often reduces bytes too, but not always.
- A prefix is declared only when it saves tokens. In very small inputs a namespace used once
  is therefore written in full (`<http://www.w3.org/2000/01/rdf-schema#subClassOf>` in the
  OWL example).

## Included Example Families

### `basic/`

A minimal RDF graph showing:

- prefixes
- `rdf:type` as `a`
- grouped subject rendering
- simple object references

### `owl/`

A small OWL-oriented example encoded as RDF triples, showing:

- classes
- subclass relations
- property declarations
- typed individuals

This illustrates how Loreto represents OWL content through its RDF graph encoding. The OWL
namespace keeps its conventional alias `owl:` because it costs no more tokens than a
single letter.

### `swrl/`

A small SWRL-oriented example encoded in RDF/OWL style, showing:

- SWRL rule resources
- body/head atoms, as RDF lists `( … )` of blank nodes `[ … ]`
- variables and class atoms

This does not introduce a special SWRL syntax for Loreto. Instead, it shows how Loreto carries the RDF representation of SWRL constructs.

## Suggested Usage

You can use these examples to:

- understand Loreto syntax quickly
- compare source RDF with Loreto output
- reference concrete fragments in papers or documentation
- check the round trip: `tests/test_loreto.py` serialises and verifies every input here under every combination of options
