"""API tests. They need loreto.py (LORETO_SRC) and the tokenizer files, as the app does."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from rdflib import Graph
from rdflib.compare import isomorphic

from app.main import app

UNICODE = 'PREFIX :<http://example.org/>\n:alice\n :name "Alicia Pérez 😀 東京"@es ;\n :note "tab\\tand ü"\n'


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def info(client):
    return client.get("/api/info").json()


def example(client, example_id):
    return client.get(f"/api/examples/{example_id}").json()


def full_text(pane):
    return "".join(pane["pieces"]) + pane["rest"]


def test_info_lists_tokenizers_and_examples(info):
    ids = [t["id"] for t in info["tokenizers"]["available"]]
    assert info["tokenizers"]["default"] == "o200k_base"
    assert {"o200k_base", "cl100k_base"} <= set(ids)
    assert info["examples"] and all("file" not in e for e in info["examples"])
    assert info["loreto_version"].startswith("2.")


@pytest.mark.parametrize("example_id", ["paper-figure", "friend-count", "swrl", "named-graphs", "ido"])
def test_compare_every_example_is_lossless(client, example_id):
    ex = example(client, example_id)
    res = client.post("/api/compare", json={"text": ex["text"], "format": ex["format"]})
    assert res.status_code == 200, res.text
    data = res.json()
    lo = data["loreto"]
    assert lo["verified"] is True
    assert lo["pane"]["tokens"] == lo["tokens"]
    by_id = {f["id"]: f for f in data["formats"]}
    assert by_id["loreto"]["tokens"] == lo["tokens"]
    # Loreto is smaller than every standard serialisation written by rdflib
    for fid in ("turtle-prefixed", "turtle", "ntriples", "jsonld", "rdfxml"):
        assert by_id[fid]["tokens"] > lo["tokens"], fid
    download = client.post("/api/download", json={"text": ex["text"], "format": ex["format"], "which": "loreto"})
    assert download.status_code == 200
    if not lo["pane"]["text_truncated"]:
        assert full_text(lo["pane"]) == download.text


def test_paper_figure_matches_the_paper(client):
    ex = example(client, "paper-figure")
    data = client.post("/api/compare", json={"text": ex["text"], "format": "turtle"}).json()
    by_id = {f["id"]: f for f in data["formats"]}
    assert by_id["input"]["tokens"] == 162     # Figure 1 of the paper: Turtle, 162 tokens
    assert data["loreto"]["tokens"] == 133     # Loreto, 133 tokens


def test_named_graphs_are_kept(client):
    ex = example(client, "named-graphs")
    data = client.post("/api/compare", json={"text": ex["text"], "format": "trig"}).json()
    assert data["input"]["named_graphs"] == 1
    assert "GRAPH " in full_text(data["loreto"]["pane"])
    merged = {f["id"]: f["merges_graphs"] for f in data["formats"]}
    assert merged["turtle"] and not merged["trig"] and not merged["nquads"]


def test_loreto_input_is_decoded(client):
    ex = example(client, "swrl")
    first = client.post("/api/compare", json={"text": ex["text"], "format": "turtle"}).json()
    loreto_text = full_text(first["loreto"]["pane"])
    for fmt in ("loreto", "auto"):
        again = client.post("/api/compare", json={"text": loreto_text, "format": fmt}).json()
        assert again["input"]["format"] == "loreto"
        assert again["input"]["triples"] == first["input"]["triples"]
        assert again["loreto"]["verified"] is True
        # Not the same text: loreto.parse does not keep the source's prefix names (ex: becomes example:).
        decoded = client.post("/api/download", json={"text": loreto_text, "format": fmt, "which": "turtle"}).text
        original = client.post("/api/download", json={"text": ex["text"], "format": "turtle", "which": "ntriples"}).text
        assert isomorphic(Graph().parse(data=decoded, format="turtle"), Graph().parse(data=original, format="nt"))


def test_options_change_the_text(client):
    ex = example(client, "paper-figure")
    flat = client.post("/api/compare", json={"text": ex["text"], "options": {"layout": "flat", "legend": True}}).json()
    text = full_text(flat["loreto"]["pane"])
    assert flat["loreto"]["layout"] == "flat"
    assert text.startswith("# Loreto RDF graph.")
    assert flat["loreto"]["verified"] is True


def test_unknown_option_is_rejected(client):
    res = client.post("/api/compare", json={"text": "<a> <b> <c> .", "options": {"bogus": True}})
    assert res.status_code == 422


def test_ablation(client):
    ex = example(client, "ido")
    data = client.post("/api/ablation", json={"text": ex["text"], "format": "xml", "tokenizer": "cl100k_base"}).json()
    rows = {r["id"]: r for r in data["rows"]}
    assert data["rows"][0]["id"] == "full" and rows["full"]["delta"] == 0
    assert rows["no-prefixes"]["delta"] > 0
    assert rows["minimal"]["tokens"] > rows["full"]["tokens"]
    assert rows["layout-flat"]["layout"] == "flat"


def test_tokenizer_matrix(client, info):
    ex = example(client, "friend-count")
    data = client.post("/api/tokenizers", json={"text": ex["text"]}).json()
    assert [r["tokenizer"]["id"] for r in data["rows"]] == [t["id"] for t in info["tokenizers"]["available"]]
    reference = next(r for r in data["rows"] if r["tokenizer"]["id"] == data["reference"]["id"])
    assert reference["loreto"] == reference["loreto_reference_text"]
    for row in data["rows"]:
        assert row["loreto"] <= row["loreto_reference_text"] + 1   # optimising for a tokeniser never costs much more


def test_view_and_unknown_view(client):
    ex = example(client, "paper-figure")
    pane = client.post("/api/view", json={"text": ex["text"], "view": "ntriples"}).json()
    assert pane["label"] == "N-Triples" and pane["tokens"] > 0
    assert client.post("/api/view", json={"text": ex["text"], "view": "nope"}).status_code == 400


def test_prefixed_turtle_is_smaller_and_equivalent(client):
    ex = example(client, "friend-count")
    data = client.post("/api/compare", json={"text": ex["text"]}).json()
    by_id = {f["id"]: f for f in data["formats"]}
    assert by_id["turtle-prefixed"]["tokens"] < by_id["turtle"]["tokens"]
    ttl = client.post("/api/download", json={"text": ex["text"], "which": "turtle-prefixed"}).text
    assert isomorphic(Graph().parse(data=ttl, format="turtle"), Graph().parse(data=ex["text"], format="turtle"))


def test_prefixed_turtle_declares_only_used_prefixes(client):
    ex = example(client, "paper-figure")
    ttl = client.post("/api/download", json={"text": ex["text"], "which": "turtle-prefixed"}).text
    # rdf:type is written as "a" and 42 as a bare integer, so rdf: and xsd: are not used.
    assert "@prefix rdf:" not in ttl and "@prefix xsd:" not in ttl
    assert isomorphic(Graph().parse(data=ttl, format="turtle"), Graph().parse(data=ex["text"], format="turtle"))


@pytest.mark.parametrize("tokenizer", ["o200k_base", "cl100k_base", "qwen3", "deepseek-v3", "llama-3.1"])
def test_token_pieces_cover_the_text(client, info, tokenizer):
    if tokenizer not in {t["id"] for t in info["tokenizers"]["available"]}:
        pytest.skip(f"{tokenizer} not installed")
    data = client.post("/api/compare", json={"text": UNICODE, "format": "loreto", "tokenizer": tokenizer}).json()
    pane = data["loreto"]["pane"]
    assert pane["rest"] == ""
    assert full_text(pane) == client.post("/api/download", json={
        "text": UNICODE, "format": "loreto", "tokenizer": tokenizer, "which": "loreto"}).text
    assert 0 < len(pane["pieces"]) <= pane["tokens"]


def test_input_errors_are_reported(client):
    res = client.post("/api/compare", json={"text": "this is not RDF", "format": "turtle"})
    assert res.status_code == 422 and "Turtle" in res.json()["detail"]
    res = client.post("/api/compare", json={"text": "   "})
    assert res.status_code == 422
    res = client.post("/api/compare", json={"text": "<a> <b> <c> .", "tokenizer": "nope"})
    assert res.status_code == 400


def test_remote_jsonld_context_is_not_fetched(client):
    doc = '{"@context": "https://schema.org", "@id": "http://example.org/a", "name": "A"}'
    res = client.post("/api/compare", json={"text": doc, "format": "json-ld"})
    assert res.status_code == 422 and "remote @context" in res.json()["detail"]


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/", "http://localhost/", "http://10.0.0.1/x.ttl",
                                 "file:///etc/passwd", "ftp://example.org/x"])
def test_private_and_non_http_urls_are_refused(client, url):
    res = client.post("/api/fetch", json={"url": url})
    assert res.status_code == 422


def test_static_page(client):
    res = client.get("/")
    assert res.status_code == 200 and "Loreto Playground" in res.text
