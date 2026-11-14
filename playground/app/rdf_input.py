"""Reading the user's RDF: format detection, parsing and fetching from a URL."""
from __future__ import annotations

import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from types import ModuleType
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

from rdflib import Dataset

from .config import Settings

# id -> label. "auto" tries the likely formats in turn; "loreto" decodes Loreto text.
INPUT_FORMATS: Dict[str, str] = {
    "auto": "Detect automatically",
    "turtle": "Turtle",
    "trig": "TriG",
    "nt": "N-Triples",
    "nquads": "N-Quads",
    "json-ld": "JSON-LD",
    "xml": "RDF/XML (OWL)",
    "n3": "N3",
    "trix": "TriX",
    "loreto": "Loreto",
}

# Relative IRIs in pasted text resolve against this base.
DEFAULT_BASE = "http://example.org/input/"

_CONTENT_TYPES = {
    "text/turtle": "turtle",
    "application/x-turtle": "turtle",
    "application/trig": "trig",
    "application/n-triples": "nt",
    "application/n-quads": "nquads",
    "application/ld+json": "json-ld",
    "application/rdf+xml": "xml",
    "application/owl+xml": "xml",   # often mislabelled RDF/XML; OWL/XML proper fails with a clear error
    "text/n3": "n3",
    "application/trix": "trix",
}

_EXTENSIONS = {
    ".ttl": "turtle", ".trig": "trig", ".nt": "nt", ".nq": "nquads", ".jsonld": "json-ld", ".json": "json-ld",
    ".rdf": "xml", ".owl": "xml", ".xml": "xml", ".n3": "n3", ".trix": "trix", ".loreto": "loreto",
}

_ACCEPT = ("text/turtle, application/rdf+xml;q=0.9, application/ld+json;q=0.8, application/n-triples;q=0.8, "
           "application/trig;q=0.7, application/n-quads;q=0.7, text/plain;q=0.2, */*;q=0.1")

_OWL_XML_RE = re.compile(r"<(?:owl:)?Ontology\b[^>]*xmlns=\"http://www\.w3\.org/2002/07/owl#\"")


class InputError(ValueError):
    """A problem with the user's input, reported to them as is."""


# ------------------------- parsing -------------------------

def format_from_name(name: str) -> Optional[str]:
    """Input format from a file name or URL path, if its extension is known."""
    path = urlsplit(name).path if "://" in name else name
    dot = path.rfind(".")
    return _EXTENSIONS.get(path[dot:].lower()) if dot >= 0 else None


def _candidates(text: str) -> List[str]:
    head = text.lstrip("﻿ \t\r\n")[:2048]
    if head.startswith("<"):
        return ["xml", "trix"]
    if head.startswith("{") or re.match(r"\[\s*[{\]]", head):   # "[ a owl:Thing ] ." is Turtle
        return ["json-ld"]
    trig_first = re.search(r"^\s*(?:GRAPH\s+)?\S+\s*\{", head, re.M) is not None
    rdf_text = ["trig", "turtle"] if trig_first else ["turtle", "trig"]
    return rdf_text + ["nquads", "loreto", "n3"]


def _reject_remote_jsonld_context(text: str) -> None:
    """rdflib would download remote @context documents. The demo does not fetch URLs
    hidden inside the input, so those documents must be inlined."""
    try:
        doc = json.loads(text)
    except ValueError:
        return   # rdflib reports the syntax error

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("@context", "@import"):
                    contexts = value if isinstance(value, list) else [value]
                    if any(isinstance(c, str) for c in contexts):
                        raise InputError("This JSON-LD uses a remote @context. The demo does not download it: "
                                         "replace it with the context itself, or use Turtle or RDF/XML.")
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(doc)


def _parse_as(text: str, fmt: str, base: str, loreto: ModuleType) -> Dataset:
    if fmt == "loreto":
        return loreto.parse(text)
    if fmt == "json-ld":
        _reject_remote_jsonld_context(text)
    ds = Dataset()
    ds.parse(data=text, format=fmt, publicID=base)
    return ds


def parse_text(text: str, fmt: str, base: Optional[str], loreto: ModuleType) -> Tuple[Dataset, str]:
    """Parse ``text`` as ``fmt``, or try the likely formats when ``fmt`` is "auto"."""
    if fmt not in INPUT_FORMATS:
        raise InputError(f"unknown input format {fmt!r}")
    if not text.strip():
        raise InputError("The input is empty.")
    if _OWL_XML_RE.search(text[:4096]):
        raise InputError("This looks like OWL/XML, which rdflib cannot read. Convert it to RDF/XML or Turtle "
                         "first, for example with ROBOT or Protégé.")
    base = base or DEFAULT_BASE
    candidates = _candidates(text) if fmt == "auto" else [fmt]
    errors: List[str] = []
    for candidate in candidates:
        try:
            return _parse_as(text, candidate, base, loreto), candidate
        except InputError:
            raise
        except Exception as e:  # noqa: BLE001 - try the next format
            errors.append(f"{INPUT_FORMATS[candidate]}: {_short(e)}")
    if fmt == "auto":
        raise InputError("Could not parse the input in any of the formats tried.\n" + "\n".join(errors))
    raise InputError(f"Could not parse the input as {INPUT_FORMATS[fmt]}: {errors[0].split(': ', 1)[1]}")


def _short(e: Exception, limit: int = 300) -> str:
    msg = " ".join(str(e).split()) or type(e).__name__
    return msg if len(msg) <= limit else msg[:limit] + "…"


# ------------------------- fetching -------------------------

@dataclass
class Fetched:
    text: str
    format: Optional[str]   # from the Content-Type or the URL, None if unknown
    url: str                # after redirects; also the base IRI
    bytes: int


def _check_host(url: str, settings: Settings) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise InputError("Only http and https URLs can be fetched.")
    if settings.allow_private_urls:
        return
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise InputError(f"Cannot resolve host {parts.hostname!r}.") from None
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global or address.is_multicast:
            raise InputError(f"The host {parts.hostname!r} is on a private or local network; the demo only "
                             "fetches public URLs.")


def fetch_url(url: str, settings: Settings) -> Fetched:
    """GET ``url`` with RDF content negotiation, following up to 5 redirects. Every hop must
    resolve to public addresses, and the body is limited to ``max_input_bytes``."""
    import httpx

    if not settings.allow_url:
        raise InputError("Fetching URLs is disabled on this server.")
    url = url.strip()
    try:
        with httpx.Client(timeout=settings.url_timeout, follow_redirects=False,
                          headers={"Accept": _ACCEPT, "User-Agent": "LoretoPlayground/0.1"}) as client:
            for _hop in range(6):
                _check_host(url, settings)
                with client.stream("GET", url) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise InputError("The server answered with a redirect without a location.")
                        url = urljoin(url, location)
                        continue
                    if response.status_code >= 400:
                        raise InputError(f"The server answered {response.status_code} for {url}.")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > settings.max_input_bytes:
                            raise InputError(f"The document is larger than "
                                             f"{settings.max_input_bytes // (1024 * 1024)} MB.")
                    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    charset = response.charset_encoding or "utf-8"
                    try:
                        text = body.decode(charset, errors="replace")
                    except LookupError:
                        text = body.decode("utf-8", errors="replace")
                    fmt = _CONTENT_TYPES.get(content_type) or format_from_name(url)
                    return Fetched(text=text, format=fmt, url=url, bytes=len(body))
            raise InputError("Too many redirects.")
    except httpx.HTTPError as e:
        raise InputError(f"Could not fetch the URL: {_short(e)}") from None
