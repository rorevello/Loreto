# Examples

This directory contains small, concrete Loreto examples meant to complement the technical specification in [`docs/format.md`](../docs/format.md).

The goal of these examples is to show what Loreto looks like in practice for different kinds of RDF/OWL content.

The `.loreto` files in this directory are intended to reflect actual converter output or close reproductions of it.

Important note:

- Loreto is primarily optimized for token reduction
- in many cases it also reduces bytes
- for very small or structurally unusual inputs, token and byte behavior do not always evolve in exactly the same way

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

This illustrates how Loreto represents OWL content through its RDF graph encoding.

### `swrl/`

A small SWRL-oriented example encoded in RDF/OWL style, showing:

- SWRL rule resources
- body/head atoms
- variables and class atoms

This does not introduce a special SWRL syntax for Loreto. Instead, it shows how Loreto carries the RDF representation of SWRL constructs.

## Suggested Usage

You can use these examples to:

- understand Loreto syntax quickly
- compare source RDF with Loreto output
- reference concrete fragments in papers or documentation
- build future parser and round-trip tests
