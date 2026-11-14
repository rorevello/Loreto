# Experiments

The material behind the evaluation of the paper. Commands are run from the repository root.
The scripts need the Playground requirements (`pip install -r playground/requirements.txt`) and,
for the Hugging Face tokenisers, `python playground/get_tokenizers.py`.

```
experiments/
├── corpus/          token study over 88 BioPortal ontologies (Table 1)
├── llm-kg-bench/    sanity check on four LLM-KG-Bench tasks
└── figures/         scripts for Figures 1 and 2
```

## Token study over the BioPortal corpus (`corpus/`)

**Corpus.** 88 ontologies from [BioPortal](https://bioportal.bioontology.org/), in RDF/XML. They
are not redistributed here, because their licences differ. [`manifest.csv`](corpus/manifest.csv)
lists each one with its BioPortal acronym, number of triples, size and the sha256 of the exact
file used. BioPortal serves the current version of each ontology
(`https://data.bioontology.org/ontologies/<ACRONYM>/download`, with an API key), so a newer
version may give slightly different counts; the sha256 identifies the file we measured. Put the
files in one folder, one sub-folder per ontology as in the manifest (`AD-DROP.owl/AD-DROP.owl`).

MEDO contains an IRI that rdflib cannot write as Turtle or N-Triples, so the comparisons with
those formats, and Table 1, use the other 87 ontologies.

**Scripts.**

- [`benchmark_corpus.py`](corpus/benchmark_corpus.py) serialises every ontology in the standard
  formats (with rdflib) and with Loreto and each of its ablations, and counts tokens with each
  tokeniser. It records tokens, bytes, serialisation time (parsing excluded) and, with
  `--verify`, whether each Loreto text parses back to the same RDF dataset.
- [`turtle_all_prefixes.py`](corpus/turtle_all_prefixes.py) adds the stronger Turtle baseline,
  with a prefix for every namespace used twice or more, and writes Table 1.

```bash
python experiments/corpus/benchmark_corpus.py --root path/to/ontologies --verify --out experiments/corpus/results/o200k_cl100k
python experiments/corpus/benchmark_corpus.py --root path/to/ontologies \
    --tokenizers "o200k_base,hf:Qwen/Qwen3-8B,hf:deepseek-ai/DeepSeek-V3,hf:NousResearch/Hermes-3-Llama-3.1-8B" \
    --variants loreto,loreto-grouped,loreto-flat --out experiments/corpus/results/hf_tokenizers
python experiments/corpus/turtle_all_prefixes.py --root path/to/ontologies
```

**Results** (`corpus/results/`).

- `o200k_cl100k/`: o200k_base and cl100k_base, every Loreto variant, verified.
  - `per_ontology.csv`: one row per ontology, tokeniser and variant: the baseline formats, the
    full Loreto method (`loreto`) and one row per ablation (`loreto-flat`, `loreto-no-prefixes`,
    …).
  - `summary_formats.csv`: mean and median tokens, bytes and time per tokeniser and variant.
    The ablation figures of the paper come from here.
  - `summary_reduction.csv`: Loreto against every rdflib baseline, per tokeniser.
- `hf_tokenizers/`: the same for the Qwen3, DeepSeek-V3 and Llama 3.1 tokenisers (full method and
  the two layouts).
- `turtle_all_prefixes/`: `table1.csv` is Table 1 of the paper; `summary.txt` adds, for each
  tokeniser, the number of ontologies where Loreto is shorter (87 of 87 against every format)
  and the smallest reductions; `per_ontology.csv` has every count.

## Sanity check on LLM-KG-Bench (`llm-kg-bench/`)

We added Loreto as an input format to the four tasks of
[LLM-KG-Bench](https://github.com/AKSW/LLM-KG-Bench) that give the model a graph and ask about it:

| Task | Graph | Question | Metric |
|---|---|---|---|
| Text2AnswerListOrganizational | organisational graph, 29 triples | a question in English | F1 of the listed answers (`combinedF1`) |
| Sparql2AnswerListOrganizational | the same graph | a SPARQL query | `combinedF1` |
| RdfConnectionExplainStatic | the same graph | the shortest path between two people | F1 of the listed IRIs (`listF1`) |
| RdfFriendCount | FOAF graph of six people, 19 triples | who is known by the most people | `recall` |

Each task has a single graph and a single question, which every run repeats, so these results
show whether models can use Loreto, not which format is best. The prompts only name the format
(for example, `in loreto format`); they do not explain its syntax.

The tasks were run at temperature 0 with six open models (Hermes-4-70B, Hermes-4.3-36B,
Llama-3.3-70B-Instruct, Llama-3.1-70B-Instruct, Mistral-Small-3.2-24B-Instruct-2506 and
Qwen3-Coder-30B-A3B-Instruct), in Turtle, JSON-LD and Loreto, and also in RDF/XML and N-Triples
in the last two tasks. In the Loreto prompts of these runs, the two blank nodes that link each
person to their department are labelled (`_b1`, `_b2`); in ConnectionExplain, some answers
listed those labels as part of the path.

**Files.**

- [`scores.csv`](llm-kg-bench/scores.csv): mean score per model, task and format, over `runs`
  runs. The scores are those recorded by LLM-KG-Bench in its result file for each run.
- [`prompt_tokens.csv`](llm-kg-bench/prompt_tokens.csv): mean length of the prompts sent and of
  the graph inside them, per task and format, with three tokenisers. The prompts are the same
  for every model; `n_prompts` is the number of prompts averaged.
- [`plot_prompt_tokens.py`](llm-kg-bench/plot_prompt_tokens.py) draws the figure of prompt
  lengths of the paper (`prompt_tokens_o200k.pdf`) from `prompt_tokens.csv`.

## Figures (`figures/`)

- [`figure1_tokens.py`](figures/figure1_tokens.py) writes Figure 1: the example of the paper in
  Turtle and in Loreto, with one coloured box per `o200k_base` token, as LaTeX
  (`tokens_turtle.tex`, `tokens_loreto.tex`, to `\input` in the paper).
- [`screenshots.py`](figures/screenshots.py) takes the screenshots of the running Playground,
  among them Figure 2 (`paper-teaser-ido.png`). It needs Playwright and a Chromium build.
