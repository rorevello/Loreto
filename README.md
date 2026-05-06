# Loreto

Loreto stands for **Logic Ontology Representation Tokenization Optimisation**.

Loreto is a compact textual serialization for RDF/OWL datasets designed to reduce token consumption for large language models while preserving the underlying graph structure needed for ontology use, retrieval tasks, and reasoning-oriented workflows.

## Why Loreto

Standard RDF serializations such as RDF/XML, Turtle, N-Triples, TriG, and JSON-LD are useful for interoperability, but they are not optimized for LLM prompt efficiency.

Loreto focuses on:

- compact namespace planning
- compact repeated IRI representation
- low syntactic overhead
- LLM-friendly graph layout
- preservation of literal lexical forms
- preservation of named graphs and dataset contexts


## Quick Start

Install the core converter dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Convert an ontology to Loreto:

```bash
python3 loreto.py input.owl
python3 loreto.py input.ttl --no-ttl
python3 loreto.py input.rdf -o output.loret
```

The converter writes:

- `input.loret`
- optionally `input.normalized.ttl`

## Repository Contents

- `loreto.py`: main RDF/OWL to Loreto converter
- `benchmark/`: benchmark suite covering size, model performance, and reasoning
- `examples/`: small RDF, OWL, and SWRL examples with Loreto outputs
- `docs/`: technical notes on the format and benchmarks
- `requirements.txt`: core runtime dependencies

## Repository Structure

```text
Loreto/
├── README.md
├── LICENSE
├── loreto.py
└── examples/
    ├── README.md
    ├── basic/
    ├── owl/
    └── swrl/
```



## Examples

Small concrete examples are available in [`examples/`](./examples):

- `examples/basic/` for a minimal RDF graph
- `examples/owl/` for OWL content encoded as RDF
- `examples/swrl/` for SWRL represented through RDF triples

