"""The tokenisers the demo offers, with token counts and token boundaries.

Counting follows loreto.TokenCounter exactly (tiktoken: no special tokens disallowed;
Hugging Face: no special tokens added), so the numbers match the Loreto CLI and the
corpus measurements of the paper.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

DEFAULT_TOKENIZER = "o200k_base"


def _utf8_len(ch: str) -> int:
    cp = ord(ch)
    return 1 if cp < 0x80 else 2 if cp < 0x800 else 3 if cp < 0x10000 else 4


@dataclass
class Tokenizer:
    id: str
    label: str
    models: str
    source: str
    _count: Callable[[str], int] = field(repr=False)
    _ends: Callable[[str, int], Tuple[List[int], int]] = field(repr=False)   # char end of the first n tokens, total

    def count(self, text: str) -> int:
        return self._count(text)

    def pieces(self, text: str, limit: int) -> Tuple[List[str], str]:
        """Split ``text`` at token boundaries, for the first ``limit`` tokens.

        Returns the pieces and the rest of the text that was not split. ``"".join(pieces) + rest``
        is always ``text``. A character split between two tokens stays in the first piece.
        """
        ends, total = self._ends(text, limit)
        pieces: List[str] = []
        prev = 0
        for end in ends:
            if end > prev:
                pieces.append(text[prev:end])
                prev = end
        rest = text[prev:]
        if rest and len(ends) >= total:   # every token was split: trailing text the offsets do not cover
            if pieces:
                pieces[-1] += rest
            else:
                pieces.append(rest)
            rest = ""
        return pieces, rest


def _tiktoken(spec: str) -> Tuple[Callable[[str], int], Callable[[str, int], Tuple[List[int], int]]]:
    import tiktoken

    enc = tiktoken.get_encoding(spec)

    def count(text: str) -> int:
        return len(enc.encode(text, disallowed_special=()))

    def ends(text: str, limit: int) -> Tuple[List[int], int]:
        ids = enc.encode(text, disallowed_special=())
        out: List[int] = []
        consumed = 0          # bytes covered by the tokens so far
        ci, cb = 0, 0         # next character and the byte where it starts
        n = len(text)
        for token_bytes in enc.decode_tokens_bytes(ids[:limit]):
            consumed += len(token_bytes)
            while cb < consumed and ci < n:
                cb += _utf8_len(text[ci])
                ci += 1
            out.append(ci)
        return out, len(ids)

    return count, ends


def _hf_file(path: Path) -> Tuple[Callable[[str], int], Callable[[str, int], Tuple[List[int], int]]]:
    from tokenizers import Tokenizer as HFTokenizer

    tk = HFTokenizer.from_file(str(path))

    def count(text: str) -> int:
        return len(tk.encode(text, add_special_tokens=False).ids)

    def ends(text: str, limit: int) -> Tuple[List[int], int]:
        enc = tk.encode(text, add_special_tokens=False)
        return [end for _start, end in enc.offsets[:limit]], len(enc.ids)

    return count, ends


class Registry:
    """Loads the tokenisers listed in config/tokenizers.json. A tokeniser whose files are
    missing is reported as unavailable instead of being replaced by another one."""

    def __init__(self, config_path: Path, tokenizers_dir: Path):
        self._items: Dict[str, Tokenizer] = {}
        self._unavailable: List[dict] = []
        for entry in json.loads(config_path.read_text(encoding="utf-8")):
            try:
                if entry["kind"] == "tiktoken":
                    count, ends = _tiktoken(entry["spec"])
                    source = f"tiktoken:{entry['spec']}"
                elif entry["kind"] == "hf-file":
                    path = tokenizers_dir / entry["path"]
                    if not path.is_file():
                        raise FileNotFoundError(f"{path} not found")
                    count, ends = _hf_file(path)
                    source = _hf_source(path.parent) or entry["path"]
                else:
                    raise ValueError(f"unknown tokenizer kind {entry['kind']!r}")
            except Exception as e:  # noqa: BLE001 - keep the other tokenisers
                log.warning("tokenizer %s unavailable: %s", entry.get("id"), e)
                self._unavailable.append({"id": entry.get("id"), "label": entry.get("label"), "error": str(e)})
                continue
            self._items[entry["id"]] = Tokenizer(entry["id"], entry["label"], entry.get("models", ""), source,
                                                 count, ends)
        if not self._items:
            raise RuntimeError("no tokenizer could be loaded")

    def get(self, tokenizer_id: Optional[str]) -> Tokenizer:
        if not tokenizer_id:
            return self.default
        try:
            return self._items[tokenizer_id]
        except KeyError:
            raise KeyError(f"unknown tokenizer {tokenizer_id!r}; available: {', '.join(self._items)}") from None

    @property
    def default(self) -> Tokenizer:
        return self._items.get(DEFAULT_TOKENIZER) or next(iter(self._items.values()))

    def all(self) -> List[Tokenizer]:
        return list(self._items.values())

    def describe(self) -> dict:
        return {
            "default": self.default.id,
            "available": [{"id": t.id, "label": t.label, "models": t.models, "source": t.source} for t in self.all()],
            "unavailable": self._unavailable,
        }


def _hf_source(folder: Path) -> Optional[str]:
    """`source` and `snapshot` from the SOURCE.txt that sits next to a tokenizer.json."""
    meta = folder / "SOURCE.txt"
    if not meta.is_file():
        return None
    fields = dict(line.split(":", 1) for line in meta.read_text(encoding="utf-8").splitlines() if ":" in line)
    source = fields.get("source", "").strip()
    snapshot = fields.get("snapshot", "").strip()
    return f"{source} @ {snapshot[:10]}" if snapshot else source or None


class RequestCounter:
    """The counter loreto.serialize expects (`count` and `name`), with a cache that lives
    for one request only. Planning counts the same short strings many times."""

    _CACHE_MAX_LEN = 512

    def __init__(self, tokenizer: Tokenizer):
        self.name = tokenizer.id
        self._count = tokenizer.count
        self._cache: Dict[str, int] = {}

    def count(self, s: str) -> int:
        if len(s) > self._CACHE_MAX_LEN:
            return self._count(s)
        v = self._cache.get(s)
        if v is None:
            v = self._cache[s] = self._count(s)
        return v
