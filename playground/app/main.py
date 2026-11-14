"""Loreto Playground: HTTP API and static front end.

    uvicorn app.main:app --port 8000

API (JSON; every POST carries the input text, so the server keeps no session):
    GET  /api/info            tokenisers, formats, examples, limits
    GET  /api/examples/{id}   text of a built-in example
    POST /api/fetch           RDF from a URL
    POST /api/compare         Loreto with the chosen options against the standard formats
    POST /api/view            one standard format, with token boundaries
    POST /api/ablation        full method against one transformation changed at a time
    POST /api/tokenizers      token cost with every tokeniser
    POST /api/download        full text of one pane
Interactive documentation: /api/docs
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .config import Settings, load_settings
from .loreto_lib import import_loreto
from .rdf_input import INPUT_FORMATS, InputError, fetch_url, format_from_name
from .service import BASELINES, INPUT_ID, Engine
from .tokenizers import Registry

log = logging.getLogger("loreto_demo")
STATIC_DIR = Path(__file__).resolve().parent / "static"


# ------------------------- request models -------------------------

InputFormat = Literal[tuple(INPUT_FORMATS)]  # type: ignore[valid-type]


class LoretoOptionsIn(BaseModel):
    """The switches of loreto.LoretoOptions; the defaults are the full method."""
    model_config = ConfigDict(extra="forbid")
    layout: Literal["auto", "grouped", "flat"] = "auto"
    aliases: Literal["auto", "short", "mnemonic"] = "auto"
    prefixes: bool = True
    id_prefixes: bool = True
    dictionary: bool = True
    lists: bool = True
    inline_bnodes: bool = True
    literal_shorthand: bool = True
    legend: bool = False
    deskolemize: bool = False


class InputSpec(BaseModel):
    text: str = Field(description="The RDF document")
    format: InputFormat = "auto"
    base: Optional[str] = Field(None, description="Base IRI for relative IRIs (the URL of a fetched document)")


class TokenizerSpec(InputSpec):
    tokenizer: Optional[str] = Field(None, description="Tokeniser id from /api/info; default o200k_base")


class CompareRequest(TokenizerSpec):
    options: LoretoOptionsIn = LoretoOptionsIn()
    view: str = Field("turtle", description="Standard format shown next to Loreto")


class ViewRequest(TokenizerSpec):
    view: str


class DownloadRequest(CompareRequest):
    which: str = Field(description="'loreto', 'input' or a standard format id")


class FetchRequest(BaseModel):
    url: str = Field(max_length=2048)


# ------------------------- application -------------------------

def _load_examples(examples_dir: Path) -> dict:
    index = examples_dir / "examples.json"
    if not index.is_file():
        return {}
    items = {}
    for item in json.loads(index.read_text(encoding="utf-8")):
        if (examples_dir / item["file"]).is_file():
            items[item["id"]] = item
        else:
            log.warning("example %s: file %s not found", item["id"], item["file"])
    return items


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loreto = import_loreto(settings.loreto_src)
        registry = Registry(settings.tokenizers_config, settings.tokenizers_dir)
        app.state.loreto = loreto
        app.state.registry = registry
        app.state.engine = Engine(settings, registry, loreto)
        app.state.examples = _load_examples(settings.examples_dir)
        log.info("Loreto %s, tokenizers: %s", loreto.__version__, ", ".join(t.id for t in registry.all()))
        yield

    app = FastAPI(title="Loreto Playground", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    max_body = 3 * settings.max_input_bytes + 1024 * 1024   # JSON escaping can grow the text

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > max_body:
            return JSONResponse({"detail": "The request is too large."}, status_code=413)
        return await call_next(request)

    @app.middleware("http")
    async def revalidate_page(request: Request, call_next):
        # The page and its scripts must be revalidated: a stale app.js next to a new
        # index.html breaks the page.
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.exception_handler(InputError)
    async def input_error(_request: Request, exc: InputError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(KeyError)
    async def key_error(_request: Request, exc: KeyError):
        return JSONResponse({"detail": exc.args[0] if exc.args else "unknown key"}, status_code=400)

    @app.exception_handler(Exception)
    async def internal_error(_request: Request, exc: Exception):
        log.exception("internal error")
        return JSONResponse({"detail": f"Internal error: {type(exc).__name__}: {str(exc)[:300]}"}, status_code=500)

    def engine(request: Request) -> Engine:
        return request.app.state.engine

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/info")
    def info(request: Request):
        state = request.app.state
        return {
            "version": __version__,
            "loreto_version": state.loreto.__version__,
            "tokenizers": state.registry.describe(),
            "input_formats": [{"id": k, "label": v} for k, v in INPUT_FORMATS.items()],
            "views": [{"id": INPUT_ID, "label": "Your input"}] + [{"id": b.id, "label": b.label} for b in BASELINES],
            "examples": [{k: v for k, v in item.items() if k != "file"} for item in state.examples.values()],
            "features": {"url": settings.allow_url},
            "limits": {"max_input_bytes": settings.max_input_bytes, "max_triples": settings.max_triples,
                       "max_view_tokens": settings.max_view_tokens},
        }

    @app.get("/api/examples/{example_id}")
    def example(example_id: str, request: Request):
        item = request.app.state.examples.get(example_id)
        if item is None:
            raise HTTPException(404, f"unknown example {example_id!r}")
        text = (settings.examples_dir / item["file"]).read_text(encoding="utf-8")
        return {"id": example_id, "format": item.get("format", "auto"), "text": text}

    @app.post("/api/fetch")
    def fetch(req: FetchRequest):
        fetched = fetch_url(req.url, settings)
        return {"text": fetched.text, "format": fetched.format or format_from_name(fetched.url) or "auto",
                "base": fetched.url, "bytes": fetched.bytes}

    @app.post("/api/compare")
    def compare(req: CompareRequest, request: Request):
        return engine(request).compare(req.text, req.format, req.base, req.tokenizer,
                                       req.options.model_dump(), req.view)

    @app.post("/api/view")
    def view(req: ViewRequest, request: Request):
        return engine(request).view(req.text, req.format, req.base, req.tokenizer, req.view)

    @app.post("/api/ablation")
    def ablation(req: TokenizerSpec, request: Request):
        return engine(request).ablation(req.text, req.format, req.base, req.tokenizer)

    @app.post("/api/tokenizers")
    def tokenizers(req: InputSpec, request: Request):
        return engine(request).tokenizers(req.text, req.format, req.base)

    @app.post("/api/download")
    def download(req: DownloadRequest, request: Request):
        text, filename = engine(request).download(req.text, req.format, req.base, req.tokenizer,
                                                  req.options.model_dump(), req.which)
        return Response(text, media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
