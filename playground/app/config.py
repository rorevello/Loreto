"""Runtime settings, read once from environment variables.

The defaults let the app run from a checkout of the repository: loreto.py in the repository
root and the Hugging Face tokenisers in playground/tokenizers/ (python get_tokenizers.py).
The Docker image sets every path explicitly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = DEMO_DIR.parent


def _path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


def _int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    loreto_src: Path            # directory that contains loreto.py
    tokenizers_dir: Path        # Hugging Face tokenizer.json files, one folder per tokeniser
    tokenizers_config: Path     # which tokenisers the demo offers
    examples_dir: Path
    max_input_bytes: int        # larger inputs are rejected
    max_triples: int
    max_view_tokens: int        # token boundaries are shown for this many tokens per pane
    max_view_chars: int         # longer texts are cut in the panes (downloads are complete)
    cache_entries: int          # parsed inputs kept in memory
    allow_url: bool             # fetch RDF from a URL given by the user
    allow_private_urls: bool    # also from loopback and private networks (local use only)
    url_timeout: float


def load_settings() -> Settings:
    return Settings(
        loreto_src=_path("LORETO_SRC", REPO_DIR),
        tokenizers_dir=_path("LORETO_DEMO_TOKENIZERS_DIR", DEMO_DIR / "tokenizers"),
        tokenizers_config=_path("LORETO_DEMO_TOKENIZERS_CONFIG", DEMO_DIR / "config" / "tokenizers.json"),
        examples_dir=_path("LORETO_DEMO_EXAMPLES_DIR", DEMO_DIR / "examples"),
        max_input_bytes=_int("LORETO_DEMO_MAX_INPUT_BYTES", 10 * 1024 * 1024),
        max_triples=_int("LORETO_DEMO_MAX_TRIPLES", 100_000),
        max_view_tokens=_int("LORETO_DEMO_MAX_VIEW_TOKENS", 20_000),
        max_view_chars=_int("LORETO_DEMO_MAX_VIEW_CHARS", 1_000_000),
        cache_entries=_int("LORETO_DEMO_CACHE_ENTRIES", 4),
        allow_url=_bool("LORETO_DEMO_ALLOW_URL", True),
        allow_private_urls=_bool("LORETO_DEMO_ALLOW_PRIVATE_URLS", False),
        url_timeout=float(os.environ.get("LORETO_DEMO_URL_TIMEOUT", "20")),
    )
