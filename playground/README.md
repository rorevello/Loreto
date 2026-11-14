# Loreto Playground

A web demo of Loreto, the token-efficient and lossless RDF serialisation for LLM prompts. You
give it an RDF document and choose the tokeniser of your model. The page then shows:

- **Token cost by format.** Loreto against your input, Turtle (two variants), TriG, N-Triples,
  N-Quads, JSON-LD and RDF/XML.
- **Side by side.** Any standard format next to Loreto, with every token coloured, so you can
  see where the tokens go.
- **Round trip.** The Loreto text is parsed back and checked to be isomorphic to the input.
  *Decode* uses the Loreto text as the new input.
- **What each transformation contributes.** A live ablation on your input: the full method
  against one switch changed at a time.
- **Across tokenisers.** Every format counted with o200k_base, cl100k_base, Qwen3, DeepSeek-V3
  and Llama 3.1. Loreto is optimised for each tokeniser, and the table also shows what the
  o200k-optimised text costs with each one.

The input can be a built-in example, pasted text, a file or a URL. Every Loreto option (layout,
alias policy, prefixes, dictionary, lists, nesting, literals, legend, de-skolemisation) can be
switched on the page, and the text updates as you switch.

A public instance runs at https://khaos.uma.es/loreto/.

## Run it with Docker

```bash
cd playground
docker compose up --build -d
# open http://localhost:8000
docker compose down
```

The image (about 220 MB) contains loreto.py, the tokenisers and the examples. **The running
container needs no network**, so it works at a conference booth without Wi-Fi. Only fetching
URLs needs the network.

The build needs the network once:

- `loreto.py` is taken from the repository root, passed as the additional build context
  `loreto` in [compose.yaml](compose.yaml);
- the tiktoken encodings (o200k_base, cl100k_base) are downloaded and checked by tiktoken;
- the Hugging Face tokenisers are downloaded by [get_tokenizers.py](get_tokenizers.py) at the
  revision listed in [config/tokenizers.json](config/tokenizers.json), and the build fails if a
  file does not match its sha256.

### Settings

Copy [.env.example](.env.example) to `.env`. The main settings:

| Variable | Default | |
|---|---|---|
| `LORETO_DEMO_BIND` | `127.0.0.1` | `0.0.0.0` makes the demo reachable from other machines |
| `LORETO_DEMO_PORT` | `8000` | host port |
| `LORETO_DEMO_ALLOW_URL` | `true` | fetching RDF from a URL |
| `LORETO_DEMO_MAX_INPUT_BYTES` | 10 MB | larger inputs are rejected |
| `LORETO_DEMO_MAX_TRIPLES` | 100,000 | |
| `WEB_CONCURRENCY` | `1` | uvicorn worker processes; each one loads every tokeniser |

Also available: `LORETO_DEMO_MAX_VIEW_TOKENS` (20,000; tokens coloured per pane),
`LORETO_DEMO_MAX_VIEW_CHARS`, `LORETO_DEMO_CACHE_ENTRIES`, `LORETO_DEMO_URL_TIMEOUT` and
`LORETO_DEMO_ALLOW_PRIVATE_URLS`.

## Run it without Docker

```bash
cd playground
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python get_tokenizers.py          # Hugging Face tokenisers into tokenizers/
.venv/bin/uvicorn app.main:app --reload --port 8000
.venv/bin/python -m pytest                  # API tests
```

By default the app reads `loreto.py` from the repository root and the tokenisers from
`playground/tokenizers/`. Use `LORETO_SRC` and `LORETO_DEMO_TOKENIZERS_DIR` to point elsewhere.
A tokeniser whose file is missing is listed as unavailable; it is never replaced by another one.

## How the numbers are computed

- **Token counts** are computed as in `loreto.TokenCounter`: tiktoken with no special tokens
  disallowed, and Hugging Face tokenisers without added special tokens. They match the Loreto
  command line and the corpus results in [`../experiments/corpus/`](../experiments/corpus). For
  example, the IDO example gives 28,279 tokens for Turtle (rdflib) and 20,131 for Loreto with
  o200k_base, as in `results/o200k_cl100k/per_ontology.csv`.
- **Standard formats** are written as in
  [`experiments/corpus/benchmark_corpus.py`](../experiments/corpus/benchmark_corpus.py): graph
  formats from the union graph, TriG and N-Quads from the dataset.
- **Two Turtle variants.**
  - *Turtle (rdflib)* is rdflib's own Turtle. rdflib invents prefixes for predicates only, so
    subjects and objects in other namespaces are written as full IRIs.
  - *Turtle, all prefixes* declares a prefix for every namespace used twice or more, and drops
    the declarations it does not use (rdflib declares `rdf:` and `xsd:` even when every type is
    written `a` and every number bare). It is the stronger baseline, and the one the summary
    uses, as in Table 1 of the paper
    ([`experiments/corpus/turtle_all_prefixes.py`](../experiments/corpus/turtle_all_prefixes.py)).
- **Lossless** means `loreto.verify`: the text parses back to a dataset isomorphic to the
  input. With de-skolemisation the comparison is against the de-skolemised dataset.
- **Serialisation time** is Loreto's own (`timings_ms.total`). It excludes parsing the input.

## API

The page is a client of a small JSON API. Every POST carries the input, so the server keeps
no session. Interactive documentation is at `/api/docs`.

| | |
|---|---|
| `GET /api/info` | tokenisers, formats, examples, limits |
| `GET /api/examples/{id}` | an example's text |
| `POST /api/fetch` | `{"url": …}` → text, format, base IRI |
| `POST /api/compare` | `{"text", "format", "tokenizer", "options", "view"}` → Loreto, every format's tokens, the round trip |
| `POST /api/view` | one standard format with token boundaries |
| `POST /api/ablation` | full method against each switch |
| `POST /api/tokenizers` | every format counted with every tokeniser |
| `POST /api/download` | full text of one pane |

```bash
curl -s localhost:8000/api/compare -H 'Content-Type: application/json' \
  -d '{"text": "@prefix ex: <http://example.org/> . ex:a ex:p ex:b , ex:c .", "tokenizer": "qwen3"}' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["loreto"]["tokens"], [(f["id"], f["tokens"]) for f in d["formats"]])'
```

## Security and privacy

- Inputs are processed in memory. The last few parsed inputs are cached
  (`LORETO_DEMO_CACHE_ENTRIES`), and nothing is written to disk. The container filesystem is
  read-only.
- **URL fetching** follows up to 5 redirects. It refuses non-HTTP schemes and any host that
  resolves to a loopback, private, link-local or multicast address, at every hop. The body is
  limited to the maximum input size.
- **JSON-LD** with a remote `@context` is rejected rather than fetched, because the URL comes
  from the input.

## Files

```
app/main.py           FastAPI app: routes, request models, error handling
app/service.py        formats, Loreto, round trip, ablation, tokeniser matrix; parsed-input cache
app/rdf_input.py      format detection, parsing (Loreto included), URL fetching
app/tokenizers.py     tokeniser registry, token counts and token boundaries
app/config.py         settings from environment variables
app/static/           the page (HTML, CSS, JS; no external resources)
config/tokenizers.json  which tokenisers are offered, with the source of each Hugging Face file
get_tokenizers.py     downloads the Hugging Face tokenisers and checks their sha256
examples/             built-in examples (examples.json lists them)
tests/test_api.py     API tests
```

To add a Hugging Face tokeniser, add an entry to [config/tokenizers.json](config/tokenizers.json)
with `kind: "hf-file"`, a `path`, the repository (`hf_repo`), a `revision` and the `sha256` of its
`tokenizer.json`, then run `get_tokenizers.py` or rebuild the image. To add an example, put the
file in `examples/` and list it in `examples/examples.json`.

## Examples and licences

- *OWL fragment*: the example of the paper's Figure 1.
- *Named graph*: the example of [`docs/format.md`](../docs/format.md).
- *SWRL*: from [`examples/swrl/`](../examples/swrl).
- *Social graph*: the FOAF graph of the LLM-KG-Bench FriendCount task
  (https://github.com/AKSW/LLM-KG-Bench).
- *Infectious Disease Ontology (IDO)*: the BioPortal version from the evaluation corpus,
  CC BY 4.0 (https://bioportal.bioontology.org/ontologies/IDO).
- The Llama 3.1 tokeniser is downloaded from `NousResearch/Hermes-3-Llama-3.1-8B` and is subject
  to the Llama 3.1 Community License.
