"""Jama MCP Server — read-only access to Jama Connect's REST API.

Every tool is named ``jama_get_*`` and every tool is read-only. The HTTP client
can only issue GET requests under ``/rest/v1/``; the one other request it ever
makes is the OAuth token exchange (``POST /rest/oauth/token``), which reads and
writes nothing in Jama. There is no code path that creates, edits, moves, or
deletes anything. The plugin's hooks auto-approve the ``jama_get_*`` glob on
that basis, so a write tool must never be added under that prefix.

Features are Jama folders: a requirement's ``feature`` is the name of its
nearest Folder ancestor in the project tree (``feature_item_type`` changes which
item type counts as a feature), falling back to the nearest Set or Component.
``jama_get_requirements`` scopes to a whole feature subtree by name or path.

Credentials come from ``~/.buddy-council/secrets.json`` (or ``BC_SECRETS_FILE``),
section ``jama``: ``client_id`` + ``client_secret`` for OAuth client credentials
(preferred — Jama → your profile → Set API Credentials), or ``username`` +
``password`` for instances that offer no API credentials. The non-secret base
URL comes from ``JAMA_BASE_URL``.

Run as an MCP server with ``uv run mcp run server.py``.
``uv run python server.py --check [--project ID]`` is a one-shot connection
test that /bc:setup runs before the server is registered.
"""

import argparse
import asyncio
import datetime
import json
import logging
import math
import os
import re
import sys
import time
import urllib.parse
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

# Every tool returns one JSON string, sent once as text. structured_output=False stops FastMCP from adding a
# structured copy of it ({"result": "<the same JSON, escaped>"}): Copilot CLI hands the model both, which
# doubles every response and pushes results past its ~20 KB inline limit at half their real size.
mcp = FastMCP("jama")

# httpx logs every request at INFO; MCP stderr should carry problems, not traffic.
logging.getLogger("httpx").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_API_PREFIX = "/rest/v1/"
_TOKEN_PATH = "/rest/oauth/token"
_PAGE_SIZE = 50  # Jama rejects maxResults above 50
_MAX_ITEMS = 20_000  # per project fetch; beyond this the result is marked truncated
_CACHE_TTL_SECONDS = 600.0
_SCHEMA_TTL_SECONDS = 3600.0  # item types are configuration, not content, and cost several large pages to read
# Measured on Jama Cloud: every request costs ~1-1.5 s of latency, and eight pages requested together take
# as long as one. So requests run concurrently, capped across every tool call the server is serving.
_MAX_CONCURRENT_REQUESTS = 8
# A tool call waits this long for a project read, then reports progress while the read carries on in the
# background — a read the client gives up on is never lost.
_SNAPSHOT_WAIT_SECONDS = 40.0
_MAX_BATCH_ITEMS = 500
_BATCH_WINDOW = 16  # items fully read together before checking whether the page is full
_BATCH_TIME_BUDGET_SECONDS = 25.0  # a jama_get_items call stops starting new windows after this
_KEYS_PER_LOOKUP = 10  # document keys per /abstractitems search; small searches parallelise better
_MAX_CACHE_ENTRIES = 20_000
_TOKEN_EXPIRY_MARGIN_SECONDS = 60.0
_RATE_LIMIT_STATUS = 429
_MAX_RATE_LIMIT_RETRIES = 3
_MAX_NETWORK_RETRIES = 2
# A connection that drops or never opens is retried; a server that is merely slow (read timeout) is not.
_TRANSIENT_NETWORK_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError, httpx.RemoteProtocolError)
_DEFAULT_RETRY_AFTER_SECONDS = 5.0
_MAX_RETRY_AFTER_SECONDS = 60.0
_MAX_ERROR_CHARS = 500
_MAX_ANCESTOR_DEPTH = 64
_MAX_RELATED_LOOKUPS = 100
_MAX_LISTED_FEATURES = 50
# Serialized requirements per jama_get_requirements response (~15k tokens), so a page always fits
# under the clients' MCP output caps (Claude Code's default is 25k tokens). Larger results page via offset.
_PAGE_CHAR_BUDGET = 60_000
_PATH_SEPARATOR = " / "
_UNKNOWN_FEATURE = "Unknown"

_CONTAINER_KIND_BY_TYPE_KEY = {"FLD": "folder", "SET": "set", "CMP": "component"}
_CONTAINER_KIND_BY_DISPLAY = {"folder": "folder", "set": "set", "component": "component"}

# Fields surfaced as canonical fields, or pure bookkeeping — never copied into raw_fields.
_BUILTIN_FIELDS = {
    "name",
    "description",
    "documentKey",
    "globalId",
    "project",
    "createdDate",
    "modifiedDate",
    "lastActivityDate",
    "createdBy",
    "modifiedBy",
}
# People and releases come back as bare ids; resolving them costs a call each and
# adds nothing to requirement analysis, so they stay out of raw_fields.
_SKIPPED_FIELD_TYPES = {"USER", "RELEASE"}

_MAPPABLE_FIELDS = ("status", "rationale", "github_url")
_DEFAULT_FIELD_MAPPING = {"status": "status", "rationale": "rationale", "github_url": ""}
_GITHUB_URL_RE = re.compile(r"https://github\.com/[^\s\"'<>,;)\]]+", re.IGNORECASE)

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


class JamaError(RuntimeError):
    """A Jama request or configuration problem, worded for the person who has to fix it."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Configuration — base URL from env, credentials from the buddy-council secrets file
# ---------------------------------------------------------------------------


def _secrets_candidates() -> list[str]:
    """Secrets-file paths to try, in priority order."""
    candidates = []
    env_path = os.environ.get("BC_SECRETS_FILE")
    if env_path:
        candidates.append(os.path.expanduser(env_path))
    candidates.append(os.path.expanduser("~/.buddy-council/secrets.json"))
    return candidates


def _load_secrets(section: str) -> tuple[dict[str, Any], str | None]:
    """Return one section of the first readable secrets file, plus that file's path."""
    for path in _secrets_candidates():
        if not os.path.exists(path):
            continue
        try:
            if (os.stat(path).st_mode & 0o077) != 0:
                print(
                    f"WARNING: {path} is group/world-accessible; run `chmod 600` on it.",
                    file=sys.stderr,
                )
            with open(path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        section_data = data.get(section) if isinstance(data, dict) else None
        return (section_data if isinstance(section_data, dict) else {}), path
    return {}, None


@dataclass(frozen=True, repr=False)
class JamaConfig:
    base_url: str
    client_id: str = ""
    client_secret: str = ""
    username: str = ""
    password: str = ""

    @property
    def auth_mode(self) -> str:
        return "oauth" if self.client_id and self.client_secret else "basic"

    def __repr__(self) -> str:  # never let a secret reach a log or traceback
        return f"JamaConfig(base_url={self.base_url!r}, auth_mode={self.auth_mode!r})"


def normalize_base_url(raw: str) -> str:
    """Reduce whatever the user pasted to the Jama root URL, refusing anything unsafe.

    Accepts the root itself, an API URL (``…/rest/v1``), or a UI deep link
    (``…/perspective.req#/items/12?projectId=3``). Plain HTTP is refused except
    on localhost, because the credentials travel with every request.
    """
    text = (raw or "").strip()
    if not text:
        raise JamaError("JAMA_BASE_URL is empty. Run /bc:setup and choose Jama as the requirements source.")
    parts = urllib.parse.urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme not in ("https", "http") or not parts.hostname:
        raise JamaError(f"'{text}' is not a Jama URL — expected something like https://yourcompany.jamacloud.com.")
    if parts.username or parts.password:
        raise JamaError("Put Jama credentials in ~/.buddy-council/secrets.json, never in the URL.")
    if scheme == "http" and parts.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise JamaError(f"Refusing to send Jama credentials over plain HTTP ({text}). Use the https:// address.")
    path = parts.path.rstrip("/")
    marker = path.lower().find("/perspective.req")
    if marker != -1:
        path = path[:marker]
    for suffix in ("/rest/v1", "/rest/latest", "/rest"):
        if path.lower().endswith(suffix):
            path = path[: -len(suffix)]
            break
    return f"{scheme}://{parts.netloc}{path}"


def load_config() -> JamaConfig:
    """Build the config, or explain exactly what is missing."""
    secrets, secrets_path = _load_secrets("jama")
    raw_url = os.environ.get("JAMA_BASE_URL") or str(secrets.get("base_url") or "")
    if not raw_url.strip():
        raise JamaError(
            "Jama is not configured: JAMA_BASE_URL is not set. "
            "Run /bc:setup and choose Jama as the requirements source."
        )
    config = JamaConfig(
        base_url=normalize_base_url(raw_url),
        client_id=str(secrets.get("client_id") or "").strip(),
        client_secret=str(secrets.get("client_secret") or ""),
        username=str(secrets.get("username") or "").strip(),
        password=str(secrets.get("password") or ""),
    )
    if not (config.client_id and config.client_secret) and not (config.username and config.password):
        where = secrets_path or " or ".join(_secrets_candidates())
        raise JamaError(
            f'No Jama credentials in {where} (section "jama"). Expected client_id + client_secret '
            "(Jama → your profile → Set API Credentials), or username + password. Run /bc:setup."
        )
    return config


# ---------------------------------------------------------------------------
# HTTP — GET-only client
# ---------------------------------------------------------------------------


@dataclass
class Collection:
    """Every page of a paginated Jama listing, merged."""

    data: list[dict[str, Any]]
    total: int
    truncated: bool
    linked: dict[str, dict[str, Any]]
    pages: int = 1


def _error_message(response: httpx.Response) -> str:
    """Jama's own error text (``meta.message``), falling back to the raw body."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
        message = meta.get("message") or body.get("message") or body.get("error_description") or body.get("error")
        if message:
            return str(message)[:_MAX_ERROR_CHARS]
    text = (response.text or "").strip()
    return text[:_MAX_ERROR_CHARS] if text else response.reason_phrase


def _retry_after_seconds(response: httpx.Response) -> float:
    """Seconds to wait after a 429, from Retry-After when present, capped."""
    try:
        seconds = float(response.headers.get("Retry-After", ""))
    except ValueError:
        seconds = _DEFAULT_RETRY_AFTER_SECONDS
    return min(max(seconds, 0.0), _MAX_RETRY_AFTER_SECONDS)


def _network_hint(base_url: str, exc: Exception) -> str:
    host = urllib.parse.urlsplit(base_url).hostname or base_url
    return (
        f"Could not reach {host} ({type(exc).__name__}: {exc}). If Jama is self-hosted, check that you are "
        "on the corporate VPN; behind a TLS-inspecting proxy, set SSL_CERT_FILE to your company CA bundle."
    )


def _describe_failure(path: str, response: httpx.Response) -> str:
    status = response.status_code
    detail = _error_message(response)
    prefix = f"GET {path} → HTTP {status}"
    if 300 <= status < 400:
        return (
            f"{prefix}: Jama redirected to {response.headers.get('location', '?')}. JAMA_BASE_URL may be wrong, "
            "or the REST API sits behind single sign-on."
        )
    if status == 401:
        return f"{prefix}: Jama no longer accepts these credentials ({detail}). Re-run /bc:setup to update them."
    if status == 403:
        return (
            f"{prefix}: this account may not read it ({detail}). Check its project permissions, and that it is "
            "enabled under Admin → REST API → Managed Access Control."
        )
    if status == 404:
        return f"{prefix}: not found ({detail}). Check the project or item id."
    if status == _RATE_LIMIT_STATUS:
        return f"{prefix}: still rate-limited after {_MAX_RATE_LIMIT_RETRIES} retries. Wait a minute or narrow the scope."
    return f"{prefix}: {detail}"


class JamaClient:
    """Read-only Jama REST client.

    ``get`` and ``get_all`` are the only ways to reach ``/rest/v1/``, and both
    issue GET. The OAuth token exchange is the single non-GET request, and it
    targets the token endpoint, not the API.
    """

    def __init__(self, config: JamaConfig, transport: httpx.AsyncBaseTransport | None = None, sleep=asyncio.sleep):
        self.config = config
        self.requests = 0
        self._sleep = sleep
        self._token: str | None = None
        self._token_deadline = 0.0
        self._token_lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(_MAX_CONCURRENT_REQUESTS)
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(30.0),
            headers={"Accept": "application/json", "User-Agent": "buddy-council-jama-mcp"},
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def _api_url(self, path: str) -> str:
        clean = path.strip("/")
        if not clean or "://" in clean or "?" in clean or ".." in clean.split("/"):
            raise JamaError(f"Refusing malformed Jama API path: {path!r}")
        return f"{self.config.base_url}{_API_PREFIX}{clean}"

    async def _bearer(self, force: bool = False) -> str:
        async with self._token_lock:
            if force or self._token is None or time.monotonic() >= self._token_deadline:
                await self._fetch_token()
            return self._token or ""

    async def _fetch_token(self) -> None:
        url = f"{self.config.base_url}{_TOKEN_PATH}"
        try:
            response = await self._http.post(
                url,
                data={"grant_type": "client_credentials"},
                auth=(self.config.client_id, self.config.client_secret),
            )
        except httpx.HTTPError as exc:
            raise JamaError(_network_hint(self.config.base_url, exc)) from None
        if response.status_code in (400, 401, 403):
            raise JamaError(
                f"Jama rejected the API client ID/secret (HTTP {response.status_code}: {_error_message(response)}). "
                "Regenerate them in Jama (your profile → Set API Credentials) and re-run /bc:setup.",
                status=response.status_code,
            )
        if response.status_code >= 300:
            raise JamaError(
                f"OAuth token request failed with HTTP {response.status_code}: {_error_message(response)}. "
                "Check that JAMA_BASE_URL is the root of your Jama instance.",
                status=response.status_code,
            )
        try:
            body = response.json()
        except ValueError:
            raise JamaError("Jama's OAuth endpoint returned non-JSON — check JAMA_BASE_URL.") from None
        token = body.get("access_token") if isinstance(body, dict) else None
        if not token:
            raise JamaError("Jama's OAuth response carried no access_token.")
        try:
            lifetime = float(body.get("expires_in") or 3600)
        except (TypeError, ValueError):
            lifetime = 3600.0
        self._token = str(token)
        self._token_deadline = time.monotonic() + max(lifetime - _TOKEN_EXPIRY_MARGIN_SECONDS, 0.0)

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET one API resource and return its JSON body."""
        url = self._api_url(path)
        query = {k: v for k, v in (params or {}).items() if v is not None}
        refreshed = False
        retries = 0
        network_retries = 0
        while True:
            kwargs: dict[str, Any] = {"params": query}
            if self.config.auth_mode == "oauth":
                kwargs["headers"] = {"Authorization": f"Bearer {await self._bearer()}"}
            else:
                kwargs["auth"] = (self.config.username, self.config.password)
            self.requests += 1
            try:
                async with self._slots:
                    response = await self._http.get(url, **kwargs)
            except _TRANSIENT_NETWORK_ERRORS as exc:
                if network_retries >= _MAX_NETWORK_RETRIES:
                    raise JamaError(_network_hint(self.config.base_url, exc)) from None
                network_retries += 1
                await self._sleep(float(network_retries))
                continue
            except httpx.HTTPError as exc:
                raise JamaError(_network_hint(self.config.base_url, exc)) from None
            status = response.status_code
            if status == _RATE_LIMIT_STATUS and retries < _MAX_RATE_LIMIT_RETRIES:
                retries += 1
                await self._sleep(_retry_after_seconds(response))
                continue
            if status == 401 and self.config.auth_mode == "oauth" and not refreshed:
                refreshed = True  # the token may have expired early; one fresh one, then give up
                await self._bearer(force=True)
                continue
            if status >= 300:
                raise JamaError(_describe_failure(path, response), status=status)
            try:
                body = response.json()
            except ValueError:
                raise JamaError(
                    f"GET {path} returned non-JSON — check that JAMA_BASE_URL ({self.config.base_url}) "
                    "is the root of your Jama instance."
                ) from None
            if not isinstance(body, dict):
                raise JamaError(f"GET {path} returned an unexpected payload shape.")
            return body

    async def get_all(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        max_items: int | None = None,
        on_page: Callable[[int, int], None] | None = None,
    ) -> Collection:
        """GET every page of a listing, merging ``linked`` objects.

        The first page says how many results there are; the remaining pages are requested together —
        bounded by the client's request cap — and merged back in order. ``on_page(done, total)`` reports
        progress for long reads.
        """
        limit = _MAX_ITEMS if max_items is None else max_items
        base = dict(params or {})
        first = await self.get(path, {**base, "startAt": 0, "maxResults": _PAGE_SIZE})
        first_count = len(_page_entries(first))
        total_raw = ((first.get("meta") or {}).get("pageInfo") or {}).get("totalResults")
        if total_raw is None:
            return await self._get_all_sequential(path, base, limit, first)
        total = int(total_raw or 0)
        # Page by what the server actually returned, in case an instance caps pages below 50.
        stride = first_count if 0 < first_count < _PAGE_SIZE and total > first_count else _PAGE_SIZE
        starts = list(range(stride, min(total, limit), stride)) if first_count else []
        pages_total = 1 + len(starts)
        done = 1
        if on_page:
            on_page(done, pages_total)

        async def fetch(start: int) -> dict[str, Any]:
            nonlocal done
            body = await self.get(path, {**base, "startAt": start, "maxResults": stride})
            done += 1
            if on_page:
                on_page(done, pages_total)
            return body

        pages = [first, *await _gather(fetch(start) for start in starts)]
        data: list[dict[str, Any]] = []
        linked: dict[str, dict[str, Any]] = {}
        for body in pages:
            data.extend(_page_entries(body))
            _merge_linked(linked, body)
        truncated = max(total, len(data)) > limit
        return Collection(data[:limit], max(total, len(data)), truncated, linked, len(pages))

    async def _get_all_sequential(
        self, path: str, base: dict[str, Any], limit: int, first: dict[str, Any]
    ) -> Collection:
        """Page one request at a time — for a listing that does not report its total."""
        data: list[dict[str, Any]] = []
        linked: dict[str, dict[str, Any]] = {}
        body, start, pages = first, 0, 1
        while True:
            chunk = _page_entries(body)
            data.extend(chunk)
            _merge_linked(linked, body)
            count = int(((body.get("meta") or {}).get("pageInfo") or {}).get("resultCount", len(chunk)) or 0)
            start += count
            if count == 0 or not chunk:
                return Collection(data, len(data), False, linked, pages)
            if len(data) >= limit:
                return Collection(data[:limit], len(data), True, linked, pages)
            body = await self.get(path, {**base, "startAt": start, "maxResults": _PAGE_SIZE})
            pages += 1


def _page_entries(body: dict[str, Any]) -> list[dict[str, Any]]:
    chunk = body.get("data") or []
    if isinstance(chunk, dict):
        chunk = [chunk]
    return [entry for entry in chunk if isinstance(entry, dict)]


def _merge_linked(linked: dict[str, dict[str, Any]], body: dict[str, Any]) -> None:
    for kind, objects in (body.get("linked") or {}).items():
        if isinstance(objects, dict):
            linked.setdefault(kind, {}).update(objects)


async def _gather(coroutines: Iterable[Awaitable[Any]]) -> list[Any]:
    """Run coroutines together; if one fails, cancel the rest instead of leaving them running."""
    tasks = [asyncio.ensure_future(coroutine) for coroutine in coroutines]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        raise


# ---------------------------------------------------------------------------
# Rich text — Jama stores descriptions as HTML
# ---------------------------------------------------------------------------

_BLOCK_TAGS = {
    "p", "div", "br", "tr", "table", "ul", "ol", "pre", "blockquote", "section",
    "h1", "h2", "h3", "h4", "h5", "h6",
}


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._anchors: list[tuple[str, int]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in ("td", "th"):
            self.parts.append(" | ")
        elif tag == "img" and attributes.get("alt"):
            self.parts.append(f"[image: {attributes['alt']}]")
        elif tag == "a":
            self._anchors.append((attributes.get("href") or "", len(self.parts)))
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._anchors:
            href, start = self._anchors.pop()
            if href.startswith(("http://", "https://")) and href not in "".join(self.parts[start:]):
                self.parts.append(f" ({href})")  # keeps link targets — GitHub doc links included
        elif tag in _BLOCK_TAGS and tag != "br":  # <br/> fires start+end; one newline is enough
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(value: Any) -> str:
    """Flatten Jama rich text to readable plain text; plain strings pass through trimmed."""
    if value is None:
        return ""
    text = str(value)
    if "<" not in text and "&" not in text:
        return text.strip()
    parser = _HTMLText()
    parser.feed(text)
    parser.close()
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in "".join(parser.parts).replace("\xa0", " ").split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def _github_urls(value: Any) -> list[str]:
    """Every https://github.com/ URL in a field value — plain text or HTML links alike."""
    if value is None:
        return []
    found: list[str] = []
    for match in _GITHUB_URL_RE.findall(str(value)):
        url = match.rstrip(".")
        if url not in found:
            found.append(url)
    return found


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _segments(value: Any) -> list[str]:
    return [segment for segment in (_norm(part) for part in str(value or "").split("/")) if segment]


def _document_key(item: dict[str, Any]) -> str:
    return str(item.get("documentKey") or (item.get("fields") or {}).get("documentKey") or "").strip()


def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Session state — one client, plus short-lived caches so scoped fetches stay cheap
# ---------------------------------------------------------------------------


@dataclass
class ProjectSnapshot:
    project_id: int
    items: dict[int, dict[str, Any]]
    total: int
    truncated: bool
    requests: int
    loaded_at: float
    fetched_at: str
    # False until a caller has been handed this read, so the call that retries after `loading`
    # receives it even with refresh=true, instead of starting yet another read.
    delivered: bool = False


@dataclass
class _ProjectLoad:
    """A project read in flight. It runs as a task of its own, so a caller that stops waiting doesn't stop it."""

    project_id: int
    started_at: float
    pages_done: int = 0
    pages_total: int = 0
    task: asyncio.Task | None = None


class SnapshotLoading(Exception):
    """A project read outlasted the caller's wait. It carries on; the next call picks it up."""

    def __init__(self, load: _ProjectLoad):
        super().__init__(f"project {load.project_id} is still loading")
        self.load = load

    def payload(self) -> dict[str, Any]:
        load = self.load
        elapsed = time.monotonic() - load.started_at
        progress = f"{load.pages_done} of {load.pages_total} pages" if load.pages_total else "waiting for the first page"
        payload: dict[str, Any] = {
            "loading": True,
            "project_id": load.project_id,
            "pages_read": load.pages_done,
            "pages_total": load.pages_total,
            "elapsed_seconds": round(elapsed),
        }
        if load.pages_done > 1 and load.pages_total:
            payload["estimated_seconds_left"] = round(elapsed / load.pages_done * (load.pages_total - load.pages_done))
        payload["summary"] = (
            f"Still reading project {load.project_id} from Jama ({progress} after {elapsed:.0f} s). The read carries "
            "on in the background: call this tool again with the same arguments to get the result. Do not switch "
            "to fetching requirements one at a time — that is slower."
        )
        return payload


class _TimedCache:
    """A dict whose entries expire after _CACHE_TTL_SECONDS."""

    def __init__(self) -> None:
        self._entries: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any) -> Any:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry[0] >= _CACHE_TTL_SECONDS:
            del self._entries[key]
            return None
        return entry[1]

    def put(self, key: Any, value: Any) -> None:
        if len(self._entries) >= _MAX_CACHE_ENTRIES:
            now = time.monotonic()
            self._entries = {k: e for k, e in self._entries.items() if now - e[0] < _CACHE_TTL_SECONDS}
            if len(self._entries) >= _MAX_CACHE_ENTRIES:
                newest = sorted(self._entries.items(), key=lambda kv: kv[1][0])[len(self._entries) // 2 :]
                self._entries = dict(newest)
        self._entries[key] = (time.monotonic(), value)


_NOT_FOUND = -1  # cached for a document key that matched no item


class _State:
    def __init__(self, client: JamaClient | None = None):
        self.client = client
        self.item_types: dict[int, dict[str, Any]] | None = None
        self.item_types_loaded_at = 0.0
        self.item_types_lock = asyncio.Lock()
        self.options: dict[int, str] = {}
        self.loaded_picklists: set[int] = set()
        self.picklist_locks: dict[int, asyncio.Lock] = {}
        self.relationship_types: dict[int, str] | None = None
        self.include_rejected = False  # the instance answered 400 to include= on relationships
        self.projects: dict[int, ProjectSnapshot] = {}
        self.loads: dict[int, _ProjectLoad] = {}
        self.key_ids = _TimedCache()  # (casefolded document key, project filter) -> item id or _NOT_FOUND
        self.items = _TimedCache()  # item id -> full item, location included
        self.abstracts = _TimedCache()  # item id -> search result (key and name) for a related item
        self.relationships = _TimedCache()  # (item id, direction) -> Collection
        self.containers = _TimedCache()  # project id -> {id: folder, set, or component}
        self.container_locks: dict[int, asyncio.Lock] = {}
        self.search_has_location: bool | None = None  # whether /abstractitems results say where an item sits


_STATE = _State()


def reset_state(client: JamaClient | None = None) -> None:
    """Drop every cache (and optionally install a client). Used by the CLI check and tests."""
    global _STATE
    _STATE = _State(client)


def _client() -> JamaClient:
    if _STATE.client is None:
        _STATE.client = JamaClient(load_config())
    return _STATE.client


async def _item_types(refresh: bool = False) -> dict[int, dict[str, Any]]:
    state = _STATE
    requested_at = time.monotonic()
    async with state.item_types_lock:  # concurrent first calls share one read
        stale = time.monotonic() - state.item_types_loaded_at > _SCHEMA_TTL_SECONDS
        reread = refresh and state.item_types_loaded_at < requested_at
        if state.item_types is None or reread or stale:
            listing = await _client().get_all("itemtypes")
            state.item_types = {int(t["id"]): t for t in listing.data if t.get("id") is not None}
            state.item_types_loaded_at = time.monotonic()
        return state.item_types


async def _project_snapshot(
    project_id: int, refresh: bool = False, background: bool = True
) -> tuple[ProjectSnapshot, bool]:
    """Every item in the project tree, cached for _CACHE_TTL_SECONDS. Returns (snapshot, cache_hit).

    A large project takes Jama a minute or more to send, longer than a client waits for one tool call. So the
    read runs as a task of its own: a tool waits up to _SNAPSHOT_WAIT_SECONDS, then raises SnapshotLoading to
    answer in time, and a later call joins the same read. With background=False (the CLI check) it waits for
    as long as the read takes.
    """
    state = _STATE
    load = state.loads.get(project_id)
    if load is not None and load.task is not None and load.task.done():
        del state.loads[project_id]
        error = None if load.task.cancelled() else load.task.exception()
        if error is not None:  # failed while nobody was waiting: report it once; the next call retries
            status = error.status if isinstance(error, JamaError) else None
            raise JamaError(f"Reading project {project_id} from Jama failed: {error} Call again to retry.", status=status)
        load = None
    if load is None:
        cached = state.projects.get(project_id)
        fresh = cached is not None and time.monotonic() - cached.loaded_at < _CACHE_TTL_SECONDS
        if cached is not None and fresh and not (refresh and cached.delivered):
            return _deliver(cached)
        load = _start_project_load(state, project_id)
    assert load.task is not None
    try:
        if background:
            await asyncio.wait_for(asyncio.shield(load.task), _SNAPSHOT_WAIT_SECONDS)
        else:
            await asyncio.shield(load.task)
    except asyncio.TimeoutError:
        if not load.task.done():
            raise SnapshotLoading(load) from None
        raise
    finally:
        if load.task.done() and state.loads.get(project_id) is load:
            del state.loads[project_id]
    return _deliver(load.task.result())


def _deliver(snapshot: ProjectSnapshot) -> tuple[ProjectSnapshot, bool]:
    """Hand a snapshot to a caller; only the first caller to receive a read is told it was read live."""
    cache_hit = snapshot.delivered
    snapshot.delivered = True
    return snapshot, cache_hit


def _start_project_load(state: _State, project_id: int) -> _ProjectLoad:
    load = _ProjectLoad(project_id, time.monotonic())
    load.task = asyncio.ensure_future(_read_project(state, load))
    load.task.add_done_callback(_observe_failure)
    state.loads[project_id] = load
    return load


def _observe_failure(task: asyncio.Task) -> None:
    """Retrieve a background read's exception so asyncio doesn't log it; the next call reports it."""
    if not task.cancelled():
        task.exception()


async def _read_project(state: _State, load: _ProjectLoad) -> ProjectSnapshot:
    def progress(done: int, total: int) -> None:
        load.pages_done, load.pages_total = done, total

    listing = await _client().get_all("items", {"project": load.project_id}, on_page=progress)
    snapshot = ProjectSnapshot(
        project_id=load.project_id,
        items={int(item["id"]): item for item in listing.data if item.get("id") is not None},
        total=listing.total,
        truncated=listing.truncated,
        requests=listing.pages,
        loaded_at=time.monotonic(),
        fetched_at=_utc_now(),
    )
    state.projects[load.project_id] = snapshot
    if state.loads.get(load.project_id) is load:
        del state.loads[load.project_id]
    return snapshot


def _fresh_snapshot(project_id: Any) -> ProjectSnapshot | None:
    """The cached tree of a project if one is still fresh — never starts a read."""
    snapshot = _STATE.projects.get(project_id) if isinstance(project_id, int) else None
    if snapshot is not None and time.monotonic() - snapshot.loaded_at < _CACHE_TTL_SECONDS:
        return snapshot
    return None


async def _relationship_types() -> dict[int, str]:
    state = _STATE
    if state.relationship_types is None:
        listing = await _client().get_all("relationshiptypes")
        state.relationship_types = {int(t["id"]): str(t.get("name") or t["id"]) for t in listing.data if t.get("id") is not None}
    return state.relationship_types


# ---------------------------------------------------------------------------
# Item types and the project tree — where "feature = folder" lives
# ---------------------------------------------------------------------------


def _type_display(item_type: dict[str, Any] | None, fallback: Any = "") -> str:
    if not item_type:
        return str(fallback)
    return str(item_type.get("display") or item_type.get("typeKey") or item_type.get("id"))


def _type_matches(item_type: dict[str, Any] | None, names: set[str]) -> bool:
    """True when the type's display name or type key is in ``names`` (already casefolded)."""
    if not item_type or not names:
        return False
    return _norm(item_type.get("display")) in names or _norm(item_type.get("typeKey")) in names


def _container_kind(item_type: dict[str, Any]) -> str | None:
    key = str(item_type.get("typeKey") or "").upper()
    return _CONTAINER_KIND_BY_TYPE_KEY.get(key) or _CONTAINER_KIND_BY_DISPLAY.get(_norm(item_type.get("display")))


def _order_key(item: dict[str, Any]) -> tuple:
    """Document order: Jama's dotted ``location.sequence`` (e.g. "2.10.3"), then id."""
    sequence = str((item.get("location") or {}).get("sequence") or "")
    parts = tuple(int(p) for p in sequence.split(".") if p.isdigit())
    return (0, parts, item.get("id", 0)) if parts else (1, (), item.get("id", 0))


class Tree:
    """The project tree rebuilt from each item's ``location.parent.item``.

    Two ways to decide a requirement's feature:

    - **nearest** (no roots): its nearest ``feature_item_type`` ancestor — a Folder by default.
    - **level** (``root_ids`` given): the container ``feature_depth`` levels below the root it sits under,
      so deeper folders roll up into one feature. Items outside every root — or inside an ``exclude_ids``
      subtree — are out of scope.
    """

    def __init__(
        self,
        items: dict[int, dict[str, Any]],
        types: dict[int, dict[str, Any]],
        feature_item_type: str = "Folder",
        root_ids: list[int] | None = None,
        feature_depth: int = 1,
        exclude_ids: list[int] | None = None,
    ):
        self.items = items
        self.types = types
        wanted = {_norm(feature_item_type) or "folder"}
        self.feature_type_ids = {tid for tid, t in types.items() if _type_matches(t, wanted)}
        if "folder" in wanted:  # Jama's folder type key, whatever the display name was localised to
            self.feature_type_ids |= {tid for tid, t in types.items() if str(t.get("typeKey") or "").upper() == "FLD"}
        self.order = sorted(items, key=lambda iid: _order_key(items[iid]))
        self.children: dict[int | None, list[int]] = {}
        for iid in self.order:
            self.children.setdefault(self.parent_id(iid), []).append(iid)
        self._feature_cache: dict[int, int | None] = {}

        requested = [int(r) for r in root_ids or []]
        self.level_mode = bool(requested)
        self.roots = [r for r in requested if r in items and self.is_container(r)]
        self.missing_roots = [r for r in requested if r not in self.roots]
        self.root_set = set(self.roots)
        self.exclude_set = {int(e) for e in exclude_ids or [] if int(e) in items}
        self.missing_excludes = [int(e) for e in exclude_ids or [] if int(e) not in items]
        self.depth = max(1, int(feature_depth or 1))
        self._labels: dict[int, str] = {}

    def parent_id(self, iid: int) -> int | None:
        parent = ((self.items[iid].get("location") or {}).get("parent") or {}).get("item")
        try:
            pid = int(parent)
        except (TypeError, ValueError):
            return None
        return pid if pid in self.items and pid != iid else None

    def kind(self, iid: int) -> str:
        """"feature", "folder", "set", "component", or "item" (anything that is not a container)."""
        item = self.items[iid]
        type_id = item.get("itemType")
        if type_id in self.feature_type_ids:
            return "feature"
        item_type = self.types.get(type_id)
        if item_type is not None:
            return _container_kind(item_type) or "item"
        return "set" if item.get("childItemType") else "item"

    def is_container(self, iid: int) -> bool:
        return self.kind(iid) != "item"

    def ancestors(self, iid: int) -> list[int]:
        """Nearest first; stops on cycles and absurd depths rather than looping."""
        chain: list[int] = []
        seen = {iid}
        current = self.parent_id(iid)
        while current is not None and current not in seen and len(chain) < _MAX_ANCESTOR_DEPTH:
            chain.append(current)
            seen.add(current)
            current = self.parent_id(current)
        return chain

    def feature_of(self, iid: int) -> int | None:
        """The item's feature container — see the class docstring for the two modes."""
        if iid not in self._feature_cache:
            self._feature_cache[iid] = self._level_feature(iid) if self.level_mode else self._nearest_feature(iid)
        return self._feature_cache[iid]

    def _nearest_feature(self, iid: int) -> int | None:
        """Nearest feature (folder) ancestor; else the nearest Set/Component; else None."""
        chain = self.ancestors(iid)
        found = next((a for a in chain if self.kind(a) == "feature"), None)
        if found is None:
            found = next((a for a in chain if self.is_container(a)), None)
        return found

    def _level_feature(self, iid: int) -> int | None:
        """The container ``depth`` levels below the item's root; the root itself for items directly in it."""
        ancestors = self.ancestors(iid)
        root_index = next((i for i, a in enumerate(ancestors) if a in self.root_set), None)
        if root_index is None:
            return None
        below_root = [a for a in reversed(ancestors[:root_index]) if self.is_container(a)]
        if not below_root:
            return ancestors[root_index]
        return below_root[min(self.depth, len(below_root)) - 1]

    def in_scope(self, iid: int) -> bool:
        """Inside a root (level mode) and outside every excluded subtree."""
        ancestors = self.ancestors(iid)
        if self.exclude_set and (iid in self.exclude_set or self.exclude_set.intersection(ancestors)):
            return False
        return not self.level_mode or bool(self.root_set.intersection(ancestors))

    def label(self, feature: int | None) -> str:
        if feature is None:
            return _UNKNOWN_FEATURE
        return self._labels.get(feature) or self.name(feature)

    def assign_labels(self, features: set[int | None]) -> None:
        """Level mode: two features that share a name get their paths as labels, so they never merge."""
        if not self.level_mode:
            return
        by_name: dict[str, list[int]] = {}
        for feature in features - {None}:
            by_name.setdefault(_norm(self.name(feature)), []).append(feature)
        for group in by_name.values():
            if len(group) < 2:
                continue
            paths = Counter(self.path(feature) for feature in group)
            for feature in group:
                path = self.path(feature)
                key = _document_key(self.items[feature]) or str(feature)
                self._labels[feature] = path if paths[path] == 1 else f"{path} ({key})"

    def nearest_container(self, iid: int) -> int | None:
        return next((a for a in self.ancestors(iid) if self.is_container(a)), None)

    def name(self, iid: int) -> str:
        item = self.items[iid]
        return html_to_text((item.get("fields") or {}).get("name")) or _document_key(item) or str(iid)

    def path(self, iid: int) -> str:
        chain = [a for a in reversed(self.ancestors(iid)) if self.is_container(a)] + [iid]
        return _PATH_SEPARATOR.join(self.name(c) for c in chain)

    def descendants(self, root: int) -> list[int]:
        found: list[int] = []
        seen: set[int] = set()
        stack = list(reversed(self.children.get(root, [])))
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            found.append(current)
            stack.extend(reversed(self.children.get(current, [])))
        return found

    def feature_ids(self) -> list[int]:
        return [iid for iid in self.order if self.kind(iid) == "feature"]


@dataclass
class Selection:
    ids: list[int]
    excluded: int
    filtered: int
    outside: int = 0


def _select_requirements(tree: Tree, item_type_exclude: list[str], item_type_filter: list[str]) -> Selection:
    """Every non-container item in scope, minus excluded types, limited to the filter when one is given."""
    exclude = {_norm(name) for name in item_type_exclude if _norm(name)}
    include = {_norm(name) for name in item_type_filter if _norm(name)}
    ids: list[int] = []
    excluded = filtered = outside = 0
    for iid in tree.order:
        if tree.is_container(iid):
            continue
        if not tree.in_scope(iid):
            outside += 1
            continue
        item_type = tree.types.get(tree.items[iid].get("itemType"))
        if exclude and _type_matches(item_type, exclude):
            excluded += 1
            continue
        if include and not _type_matches(item_type, include):
            filtered += 1
            continue
        ids.append(iid)
    return Selection(ids, excluded, filtered, outside)


@dataclass
class ScopeResult:
    ids: list[int]
    kind: str  # "all" | "requirement" | "feature" | "none"
    matched: list[str]
    warning: str | None


def _match_containers(tree: Tree, scope: str, preferred: set[int]) -> list[int]:
    """Containers named ``scope`` — or, for "A/B" style input, whose path ends in those segments.

    ``preferred`` containers (the features) win over other containers with the same name. In level mode
    only containers inside a root can match, so a same-named folder elsewhere in the project never does.
    """
    target = _norm(scope)
    wanted_segments = _segments(scope)
    containers = [
        iid
        for iid in tree.order
        if tree.is_container(iid) and (not tree.level_mode or iid in tree.root_set or tree.in_scope(iid))
    ]
    matches = [c for c in containers if target in (_norm(tree.name(c)), _norm(tree.label(c)))]
    if not matches and len(wanted_segments) > 1:
        matches = [c for c in containers if _segments(tree.path(c))[-len(wanted_segments):] == wanted_segments]
    favoured = [c for c in matches if c in preferred]
    return favoured or matches


def _apply_scope(tree: Tree, selected: list[int], scope: str) -> ScopeResult:
    """Excel-provider semantics, extended to a real tree.

    - empty / "all" → everything selected
    - a document key (or Jama id) → that requirement plus every requirement in the same feature
    - a feature name or path → every requirement anywhere under that folder, sub-folders included
    """
    text = (scope or "").strip()
    if not text or text.casefold() == "all":
        return ScopeResult(selected, "all", [], None)

    by_key = {}
    for iid in selected:
        key = _document_key(tree.items[iid])
        if key:
            by_key.setdefault(key.casefold(), iid)
    hit = by_key.get(text.casefold())
    if hit is None and text.isdigit() and int(text) in set(selected):
        hit = int(text)
    if hit is not None:
        feature = tree.feature_of(hit)
        ids = [iid for iid in selected if tree.feature_of(iid) == feature]
        return ScopeResult(ids, "requirement", [tree.path(feature)] if feature is not None else [], None)

    if tree.level_mode:
        features_in_play = {tree.feature_of(iid) for iid in selected} - {None}
    else:
        features_in_play = {iid for iid in tree.order if tree.kind(iid) == "feature"}
    containers = _match_containers(tree, text, features_in_play)
    if containers:
        inside: set[int] = set()
        for container in containers:
            inside.update(tree.descendants(container))
        ids = [iid for iid in selected if iid in inside]
        warning = None
        if len(containers) > 1:
            warning = (
                f"'{text}' matches {len(containers)} features; returning all of them. "
                f"Pass a path such as '{tree.path(containers[0])}' to pick one."
            )
        return ScopeResult(ids, "feature", [tree.path(c) for c in containers], warning)

    if tree.level_mode:
        names = sorted({tree.label(f) for f in features_in_play})
    else:
        names = sorted({tree.name(c) for c in (tree.feature_ids() or [i for i in tree.order if tree.is_container(i)])})
    listed = ", ".join(names[:_MAX_LISTED_FEATURES]) + (" …" if len(names) > _MAX_LISTED_FEATURES else "")
    return ScopeResult(
        [],
        "none",
        [],
        f"Scope '{text}' matched no requirement ID or feature; returning 0 requirements. Available features: {listed or '(none)'}",
    )


# ---------------------------------------------------------------------------
# Fields — definitions, pick-list resolution, rendering to the canonical schema
# ---------------------------------------------------------------------------


def _field_defs(item_type: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Field definitions keyed by name, and by name without Jama's ``$<itemTypeId>`` suffix."""
    defs: dict[str, dict[str, Any]] = {}
    for field_def in (item_type or {}).get("fields") or []:
        if isinstance(field_def, dict) and field_def.get("name"):
            name = str(field_def["name"])
            defs[name] = field_def
            defs.setdefault(name.split("$", 1)[0], field_def)
    return defs


def _def_for(defs: dict[str, dict[str, Any]], key: str) -> dict[str, Any] | None:
    return defs.get(key) or defs.get(key.split("$", 1)[0])


def _field_key(fields: dict[str, Any], defs: dict[str, dict[str, Any]], wanted: str) -> str | None:
    """The item's field key whose label or name matches ``wanted`` (case-insensitive)."""
    target = _norm(wanted)
    if not target:
        return None
    for key in fields:
        field_def = _def_for(defs, key) or {}
        if target in (_norm(field_def.get("label")), _norm(key), _norm(key.split("$", 1)[0])):
            return key
    return None


def _field_mapping(overrides: dict[str, str] | None) -> tuple[dict[str, str], list[str]]:
    mapping = dict(_DEFAULT_FIELD_MAPPING)
    ignored: list[str] = []
    for key, value in (overrides or {}).items():
        if key in mapping:
            mapping[key] = str(value or "")
        else:
            ignored.append(key)
    return mapping, ignored


def _lookup_ids(items: list[dict[str, Any]], types: dict[int, dict[str, Any]]) -> dict[int | None, set[int]]:
    """Pick-list option ids used by these items, grouped by pick list."""
    wanted: dict[int | None, set[int]] = {}
    for item in items:
        defs = _field_defs(types.get(item.get("itemType")))
        for key, value in (item.get("fields") or {}).items():
            field_def = _def_for(defs, key)
            if not field_def:
                continue
            field_type = str(field_def.get("fieldType") or "").upper()
            if field_type == "LOOKUP" and isinstance(value, int) and not isinstance(value, bool):
                option_ids = [value]
            elif field_type == "MULTI_LOOKUP" and isinstance(value, list):
                option_ids = [v for v in value if isinstance(v, int) and not isinstance(v, bool)]
            else:
                continue
            pick_list = field_def.get("pickList")
            wanted.setdefault(pick_list if isinstance(pick_list, int) else None, set()).update(option_ids)
    return wanted


async def _load_options(wanted: dict[int | None, set[int]]) -> None:
    """Resolve pick-list option ids to names: one call per pick list, all lists at once; per-option only as a fallback."""
    state = _STATE
    client = _client()

    async def load_list(pick_list: int) -> None:
        async with state.picklist_locks.setdefault(pick_list, asyncio.Lock()):
            if pick_list in state.loaded_picklists:
                return
            try:
                listing = await client.get_all(f"picklists/{pick_list}/options")
            except JamaError as exc:
                if exc.status not in (403, 404):
                    raise
            else:
                for option in listing.data:
                    if option.get("id") is not None:
                        state.options[int(option["id"])] = str(option.get("name") or option.get("value") or option["id"])
            state.loaded_picklists.add(pick_list)

    async def load_option(option_id: int) -> None:
        try:
            body = await client.get(f"picklistoptions/{option_id}")
        except JamaError as exc:
            if exc.status not in (403, 404):
                raise
            state.options[option_id] = str(option_id)
            return
        data = body.get("data") or {}
        state.options[option_id] = str(data.get("name") or data.get("value") or option_id)

    lists = [p for p, ids in wanted.items() if p is not None and any(o not in state.options for o in ids)]
    await _gather(load_list(p) for p in lists)
    missing = sorted({o for ids in wanted.values() for o in ids if o not in state.options})
    await _gather(load_option(o) for o in missing)


def _render_value(value: Any, field_def: dict[str, Any] | None) -> Any:
    """A field value as a reader would want it: pick lists by name, rich text as plain text."""
    if value is None or value == "" or value == []:
        return None
    field_type = str((field_def or {}).get("fieldType") or "").upper()
    if field_type == "LOOKUP" and isinstance(value, int) and not isinstance(value, bool):
        return _STATE.options.get(value, str(value))
    if field_type == "MULTI_LOOKUP" and isinstance(value, list):
        return [_STATE.options.get(v, str(v)) for v in value if isinstance(v, int)] or None
    if isinstance(value, str):
        return html_to_text(value) or None
    if isinstance(value, (bool, int, float)):
        return value
    return None


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _render_requirement(
    tree: Tree,
    iid: int,
    mapping: dict[str, str],
    include_custom_fields: bool,
    base_url: str,
    project_id: int | None,
) -> dict[str, Any]:
    """One Jama item in the plugin's canonical requirement schema (same shape as the Excel parser)."""
    item = tree.items[iid]
    fields = item.get("fields") or {}
    item_type = tree.types.get(item.get("itemType"))
    defs = _field_defs(item_type)
    keys = {canonical: _field_key(fields, defs, wanted) for canonical, wanted in mapping.items()}

    def mapped(canonical: str) -> Any:
        key = keys.get(canonical)
        return _render_value(fields.get(key), _def_for(defs, key)) if key else None

    feature = tree.feature_of(iid)
    project = item.get("project") or project_id
    raw: dict[str, Any] = {
        "jama_id": iid,
        "item_type": _type_display(item_type, item.get("itemType")),
        "feature_path": tree.path(feature) if feature is not None else "",
        "sequence": (item.get("location") or {}).get("sequence"),
        "modified_date": item.get("modifiedDate") or fields.get("modifiedDate"),
        "url": f"{base_url}/perspective.req#/items/{iid}?projectId={project}",
    }
    if tree.level_mode:
        nearest = tree.nearest_container(iid)
        raw["folder_path"] = tree.path(nearest) if nearest is not None else ""
    if include_custom_fields:
        mapped_keys = {key for key in keys.values() if key}
        for key, value in fields.items():
            if key in _BUILTIN_FIELDS or key in mapped_keys:
                continue
            field_def = _def_for(defs, key)
            if field_def and str(field_def.get("fieldType") or "").upper() in _SKIPPED_FIELD_TYPES:
                continue
            rendered = _render_value(value, field_def)
            if rendered is None:
                continue
            label = str((field_def or {}).get("label") or key.split("$", 1)[0])
            raw[f"{label} ({key})" if label in raw else label] = rendered

    requirement: dict[str, Any] = {
        "type": "requirement",
        "id": _document_key(item) or str(iid),
        "title": _as_text(_render_value(fields.get("name"), None)),
        "description": html_to_text(fields.get("description")),
        "rationale": _as_text(mapped("rationale")),
        "feature": tree.label(feature),
        "status": _as_text(mapped("status")),
        "linked_ids": [],
        "raw_fields": raw,
    }
    github_key = keys.get("github_url")
    if github_key:
        urls = _github_urls(fields.get(github_key))
        if urls:
            requirement["_enrichment_urls"] = [{"source": "github", "url": url} for url in urls]
    return requirement


# ---------------------------------------------------------------------------
# Shared tool helpers
# ---------------------------------------------------------------------------


def _user_summary(user: dict[str, Any]) -> dict[str, Any]:
    name = " ".join(part for part in (user.get("firstName"), user.get("lastName")) if part)
    return {
        "id": user.get("id"),
        "username": user.get("username"),
        "name": name or user.get("username"),
        "license_type": user.get("licenseType"),
        "active": user.get("active"),
    }


async def _list_projects() -> list[dict[str, Any]]:
    listing = await _client().get_all("projects")
    projects = []
    for project in listing.data:
        if project.get("isFolder"):
            continue
        fields = project.get("fields") or {}
        projects.append(
            {
                "id": project.get("id"),
                "key": project.get("projectKey") or fields.get("projectKey"),
                "name": fields.get("name") or project.get("name"),
            }
        )
    return sorted(projects, key=lambda p: _norm(p["name"]))


def _snapshot_notes(snapshot: ProjectSnapshot, tree: Tree, feature_item_type: str, cache_hit: bool) -> tuple[str, list[str]]:
    """A source line for the summary, plus warnings that change how results should be read."""
    if cache_hit:
        source = f"project tree cached at {snapshot.fetched_at} (pass refresh=true to re-read Jama)"
    else:
        source = f"project tree read live ({len(snapshot.items)} items, {snapshot.requests} requests)"
    warnings: list[str] = []
    if snapshot.truncated:
        warnings.append(
            f"Project {snapshot.project_id} has {snapshot.total} items; only the first {len(snapshot.items)} were "
            "read, so results are incomplete and some features may be missing."
        )
    if not snapshot.items:
        warnings.append(
            f"Project {snapshot.project_id} returned no items — check the project id and that this account can read it."
        )
    elif tree.level_mode:
        if tree.missing_roots:
            warnings.append(
                f"Requirements root(s) {tree.missing_roots} are not containers in project {snapshot.project_id} — "
                "moved or deleted. Re-run /bc:setup to re-anchor on your test suite."
            )
        if not tree.roots:
            warnings.append("No configured requirements root exists, so nothing is in scope.")
        if tree.missing_excludes:
            warnings.append(f"Excluded folder(s) {tree.missing_excludes} no longer exist; nothing was excluded for them.")
    elif not tree.feature_type_ids:
        warnings.append(
            f"No item type is named '{feature_item_type}', so requirements are grouped by their nearest Set or Component."
        )
    elif not tree.feature_ids():
        warnings.append(
            f"Project {snapshot.project_id} has no '{feature_item_type}' items, so requirements are grouped by their "
            "nearest Set or Component."
        )
    return source, warnings


# ---------------------------------------------------------------------------
# Items by key — batched searches, shared caches, one round of requests per tree level
# ---------------------------------------------------------------------------


@dataclass
class _Loaded:
    """One requested key after its window was read: an item id, an error, or neither (not found)."""

    key: str
    item_id: int | None = None
    error: JamaError | None = None


@dataclass
class _ItemBatch:
    """Everything read for a run of requested keys, ready to render."""

    loaded: list[_Loaded] = field(default_factory=list)
    types: dict[int, dict[str, Any]] = field(default_factory=dict)
    items: dict[int, dict[str, Any]] = field(default_factory=dict)  # requested items plus every ancestor
    related: dict[int, dict[str, list[dict[str, Any]]]] = field(default_factory=dict)
    warnings: dict[int, list[str]] = field(default_factory=dict)  # per requested item
    notes: list[str] = field(default_factory=list)  # about the batch as a whole


def _parent_of(item: dict[str, Any]) -> int | None:
    parent = ((item.get("location") or {}).get("parent") or {}).get("item")
    try:
        return int(parent)
    except (TypeError, ValueError):
        return None


def _is_full_item(entry: Any) -> bool:
    """A search result that already carries what GET /items/{id} would: its fields and its place in the tree."""
    return isinstance(entry, dict) and isinstance(entry.get("fields"), dict) and isinstance(entry.get("location"), dict)


def _unique_keys(values: list[str] | str) -> list[str]:
    """Requested keys in order, without case-insensitive repeats."""
    seen: dict[str, str] = {}
    for key in _parse_keys(values):
        seen.setdefault(key.casefold(), key)
    return list(seen.values())


async def _resolve_keys(
    keys: list[str], project_id: int | None, refresh: bool
) -> tuple[dict[str, int], dict[str, JamaError], dict[int, dict[str, Any]]]:
    """Document keys (and numeric ids) to item ids.

    Keys are searched _KEYS_PER_LOOKUP to a request, every request at once; spellings that match nothing are
    retried upper-cased. Returns (ids by key, errors by key, full items the searches already returned). A key
    in neither dict was not found.
    """
    state = _STATE
    client = _client()
    resolved: dict[str, int] = {}
    errors: dict[str, JamaError] = {}
    full: dict[int, dict[str, Any]] = {}
    pending: list[str] = []
    for key in keys:
        if key.isdigit():
            resolved[key] = int(key)
            continue
        cached = None if refresh else state.key_ids.get((key.casefold(), project_id))
        if cached is None:
            pending.append(key)
        elif cached != _NOT_FOUND:
            resolved[key] = cached

    async def search(chunk: list[tuple[str, str]]) -> None:
        params: dict[str, Any] = {"documentKey": [text for text, _ in chunk]}
        if project_id is not None:
            params["project"] = project_id
        try:
            listing = await client.get_all("abstractitems", params)
        except JamaError as exc:
            for _, key in chunk:
                errors[key] = exc
            return
        by_key: dict[str, dict[str, Any]] = {}
        for entry in listing.data:
            document_key = _document_key(entry)
            if document_key and entry.get("id") is not None:
                by_key.setdefault(document_key.casefold(), entry)
        for _, key in chunk:
            entry = by_key.get(key.casefold())
            if entry is None:
                continue
            item_id = int(entry["id"])
            resolved[key] = item_id
            state.abstracts.put(item_id, entry)
            if _is_full_item(entry):
                state.items.put(item_id, entry)
                full[item_id] = entry

    for attempt in ("as given", "upper-cased"):
        if attempt == "as given":
            spellings = [(key, key) for key in pending]
        else:
            spellings = [
                (key.upper(), key) for key in pending if key not in resolved and key not in errors and key.upper() != key
            ]
        chunks = [spellings[i : i + _KEYS_PER_LOOKUP] for i in range(0, len(spellings), _KEYS_PER_LOOKUP)]
        await _gather(search(chunk) for chunk in chunks)
    for key in pending:
        if key in resolved:
            state.key_ids.put((key.casefold(), project_id), resolved[key])
        elif key not in errors:
            state.key_ids.put((key.casefold(), project_id), _NOT_FOUND)
    return resolved, errors, full


async def _fetch_items(ids: Iterable[int], refresh: bool) -> tuple[dict[int, dict[str, Any]], dict[int, JamaError]]:
    """Full items by id: from the cache, else one GET each, all at once."""
    state = _STATE
    client = _client()
    found: dict[int, dict[str, Any]] = {}
    errors: dict[int, JamaError] = {}
    missing: list[int] = []
    for item_id in dict.fromkeys(ids):
        cached = None if refresh else state.items.get(item_id)
        if cached is None:
            missing.append(item_id)
        else:
            found[item_id] = cached

    async def fetch(item_id: int) -> None:
        try:
            data = (await client.get(f"items/{item_id}")).get("data")
        except JamaError as exc:
            errors[item_id] = exc
            return
        if isinstance(data, dict) and data.get("id") is not None:
            state.items.put(item_id, data)
            found[item_id] = data
        else:
            errors[item_id] = JamaError(f"Jama returned no item for id {item_id}.", status=404)

    await _gather(fetch(item_id) for item_id in missing)
    return found, errors


async def _containers(project_id: int) -> dict[int, dict[str, Any]]:
    """Every folder, set, and component in a project — enough to place any of its items in the tree.

    One search for the container item types, its pages read together and cached, replaces walking up from
    each item a parent at a time (a request per tree level). Empty when it cannot help: on an instance whose
    search results don't say where an item sits, or when the search fails — the walk then does the work.
    """
    state = _STATE
    async with state.container_locks.setdefault(project_id, asyncio.Lock()):
        cached = state.containers.get(project_id)
        if cached is not None:
            return cached
        containers: dict[int, dict[str, Any]] = {}
        kinds = sorted(tid for tid, t in (await _item_types()).items() if _container_kind(t))
        if kinds and state.search_has_location is not False:
            try:
                listing = await _client().get_all("abstractitems", {"project": project_id, "itemType": kinds})
            except JamaError:
                listing = None
            entries = [e for e in (listing.data if listing else []) if e.get("id") is not None]
            if entries:
                state.search_has_location = all(isinstance(e.get("location"), dict) for e in entries)
                if state.search_has_location:
                    containers = {int(e["id"]): e for e in entries}
        state.containers.put(project_id, containers)
        return containers


async def _add_ancestors(items: dict[int, dict[str, Any]], start: Iterable[int]) -> list[str]:
    """Add every ancestor of `start` to `items` — one round of requests per tree level, a shared parent read once."""
    warnings: list[str] = []
    frontier = [item_id for item_id in start if item_id in items]
    for _ in range(_MAX_ANCESTOR_DEPTH):
        parents = {p for p in (_parent_of(items[i]) for i in frontier) if p is not None and p not in items}
        if not parents:
            break
        found, errors = await _fetch_items(parents, refresh=False)
        items.update(found)
        warnings.extend(
            f"Could not read container {item_id} ({exc}); feature paths through it are incomplete."
            for item_id, exc in sorted(errors.items())
        )
        frontier = list(found)
    return warnings


async def _relationship_listing(item_id: int, direction: str, refresh: bool) -> Collection:
    """One item's upstream or downstream relationships, cached."""
    state = _STATE
    cached = None if refresh else state.relationships.get((item_id, direction))
    if cached is not None:
        return cached
    client = _client()
    path = f"items/{item_id}/{direction}relationships"
    listing = None
    if not state.include_rejected:
        other_side = "data.fromItem" if direction == "upstream" else "data.toItem"  # the item itself is known
        try:
            listing = await client.get_all(path, {"include": [other_side]})
        except JamaError as exc:
            if exc.status != 400:
                raise
            state.include_rejected = True  # an instance that rejects include= still answers plainly; stop asking
    if listing is None:
        listing = await client.get_all(path)
    state.relationships.put((item_id, direction), listing)
    return listing


async def _traceability(
    ids: list[int], known: dict[int, dict[str, Any]], refresh: bool
) -> tuple[dict[int, dict[str, list[dict[str, Any]]]], dict[int, list[str]]]:
    """Upstream and downstream relationships of many items at once, each unnamed related item looked up once."""
    state = _STATE
    client = _client()
    sides = {"upstream": "fromItem", "downstream": "toItem"}
    listings: dict[tuple[int, str], Collection] = {}
    warnings: dict[int, list[str]] = {}
    relationship_types: dict[int, str] = {}

    async def read(item_id: int, direction: str) -> None:
        try:
            listings[(item_id, direction)] = await _relationship_listing(item_id, direction, refresh)
        except JamaError as exc:
            warnings.setdefault(item_id, []).append(f"Its {direction} relationships could not be read: {exc}")

    async def read_types() -> None:
        nonlocal relationship_types
        try:
            relationship_types = await _relationship_types()
        except JamaError as exc:
            if exc.status not in (403, 404):
                raise

    await _gather([read_types(), *(read(i, d) for i in ids for d in sides)])

    names: dict[int, dict[str, Any]] = {}
    for listing in listings.values():
        for key, info in (listing.linked.get("items") or {}).items():
            if isinstance(info, dict) and str(key).isdigit():
                names[int(key)] = info
    wanted: list[int] = []
    for (_, direction), listing in listings.items():
        for relationship in listing.data:
            other = relationship.get(sides[direction])
            if isinstance(other, int) and other not in names:
                wanted.append(other)
    lookups: list[int] = []
    for other in dict.fromkeys(wanted):
        cached = known.get(other) or state.items.get(other) or state.abstracts.get(other)
        if cached is not None:
            names[other] = cached
        else:
            lookups.append(other)
    skipped = set(lookups[_MAX_RELATED_LOOKUPS:])
    failed: set[int] = set()

    async def look_up(other: int) -> None:
        try:
            data = (await client.get(f"abstractitems/{other}")).get("data")
        except JamaError as exc:
            if exc.status not in (403, 404):
                failed.add(other)
            return
        if isinstance(data, dict):
            state.abstracts.put(other, data)
            names[other] = data

    await _gather(look_up(other) for other in lookups[:_MAX_RELATED_LOOKUPS])

    related: dict[int, dict[str, list[dict[str, Any]]]] = {i: {"upstream": [], "downstream": []} for i in ids}
    for (item_id, direction), listing in listings.items():
        bounded = unreadable = 0
        for relationship in listing.data:
            other = relationship.get(sides[direction])
            if not isinstance(other, int):
                continue
            bounded += other in skipped
            unreadable += other in failed
            info = names.get(other) or {}
            type_id = relationship.get("relationshipType")
            related[item_id][direction].append(
                {
                    "id": _document_key(info) or str(other),
                    "jama_id": other,
                    "title": html_to_text((info.get("fields") or {}).get("name")),
                    "relationship": relationship_types.get(type_id, str(type_id)) if type_id is not None else "",
                    "suspect": bool(relationship.get("suspect")),
                }
            )
        if bounded:
            warnings.setdefault(item_id, []).append(
                f"{bounded} {direction} related items were left as Jama ids to bound the number of requests."
            )
        if unreadable:
            warnings.setdefault(item_id, []).append(
                f"{unreadable} {direction} related items could not be looked up, so they are shown as Jama ids."
            )
    return related, warnings


async def _read_window(
    keys: list[str],
    project_id: int | None,
    include_relationships: bool,
    refresh: bool,
    batch: _ItemBatch,
) -> None:
    """Read a run of requested keys into `batch`: search, items, then ancestors, traceability, and pick lists at once."""
    searched: list[tuple[dict[str, int], dict[str, JamaError], dict[int, dict[str, Any]]]] = []

    async def search() -> None:
        searched.append(await _resolve_keys(keys, project_id, refresh))

    async def item_types() -> None:
        batch.types = await _item_types()

    async def place(project: int) -> None:
        """Seed the tree around a project's items: its cached full tree, else its containers."""
        snapshot = _fresh_snapshot(project)
        known = snapshot.items if snapshot is not None else await _containers(project)
        for item_id, item in known.items():
            batch.items.setdefault(item_id, item)

    await _gather([search(), item_types()])
    [(resolved, errors, full)] = searched
    ids = list(dict.fromkeys(resolved[key] for key in keys if key in resolved))
    found, item_errors = await _fetch_items([i for i in ids if i not in full], refresh)
    found.update({i: full[i] for i in ids if i in full})
    batch.items.update(found)
    types = batch.types
    projects = {p for p in (item.get("project") for item in found.values()) if isinstance(p, int)}

    related: dict[int, dict[str, list[dict[str, Any]]]] = {}
    trace_warnings: dict[int, list[str]] = {}
    ancestor_warnings: list[str] = []

    async def ancestors() -> None:  # only this step needs the containers; the others don't wait for them
        await _gather(place(p) for p in sorted(projects))
        ancestor_warnings.extend(await _add_ancestors(batch.items, found))

    async def traceability() -> None:
        nonlocal related, trace_warnings
        related, trace_warnings = await _traceability(list(found), batch.items, refresh)

    steps = [ancestors(), _load_options(_lookup_ids(list(found.values()), types))]
    if include_relationships:
        steps.append(traceability())
    await _gather(steps)

    batch.related.update(related)
    for item_id, messages in trace_warnings.items():
        batch.warnings.setdefault(item_id, []).extend(messages)
    batch.notes.extend(w for w in ancestor_warnings if w not in batch.notes)
    for key in keys:
        item_id = resolved.get(key)
        if key in errors:
            batch.loaded.append(_Loaded(key, error=errors[key]))
        elif item_id is None:
            batch.loaded.append(_Loaded(key))
        elif item_id in item_errors:
            error = item_errors[item_id]
            batch.loaded.append(_Loaded(key) if error.status == 404 else _Loaded(key, error=error))
        else:
            batch.loaded.append(_Loaded(key, item_id))


def _render_batch(
    batch: _ItemBatch,
    feature_item_type: str,
    root_ids: list[int] | None,
    feature_depth: int,
    mapping: dict[str, str],
    include_custom_fields: bool,
    include_relationships: bool,
    project_id: int | None,
) -> list[tuple[_Loaded, dict[str, Any] | None]]:
    """Each requested key with its rendered entry (None when not found or failed), in request order."""
    tree = Tree(batch.items, batch.types, feature_item_type, root_ids, feature_depth)
    tree.assign_labels({tree.feature_of(loaded.item_id) for loaded in batch.loaded if loaded.item_id is not None})
    base_url = _client().config.base_url
    rendered: list[tuple[_Loaded, dict[str, Any] | None]] = []
    for loaded in batch.loaded:
        if loaded.item_id is None:
            rendered.append((loaded, None))
            continue
        requirement = _render_requirement(tree, loaded.item_id, mapping, include_custom_fields, base_url, project_id)
        entry: dict[str, Any] = {"requirement": requirement}
        if include_relationships:
            relationships = batch.related.get(loaded.item_id) or {"upstream": [], "downstream": []}
            requirement["linked_ids"] = list(
                dict.fromkeys(rel["id"] for rel in relationships["upstream"] + relationships["downstream"])
            )
            entry["relationships"] = relationships
        warnings = list(batch.warnings.get(loaded.item_id, []))
        if tree.level_mode and tree.feature_of(loaded.item_id) is None:
            warnings.append(f"{requirement['id']} sits outside the configured requirements root, so analysis commands ignore it.")
        if warnings:
            entry["warnings"] = warnings
        rendered.append((loaded, entry))
    return rendered


# ---------------------------------------------------------------------------
# Tools — all read-only, all named jama_get_*
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READ_ONLY, structured_output=False)
async def jama_get_current_user() -> str:
    """Check the Jama connection: the account this server authenticates as, and the auth method in use."""
    client = _client()
    user = (await client.get("users/current")).get("data") or {}
    return _dump({"base_url": client.config.base_url, "auth": client.config.auth_mode, "user": _user_summary(user)})


@mcp.tool(annotations=_READ_ONLY, structured_output=False)
async def jama_get_projects() -> str:
    """List the Jama projects this account can read: id, key, and name (project folders omitted)."""
    return _dump({"projects": await _list_projects()})


@mcp.tool(annotations=_READ_ONLY, structured_output=False)
async def jama_get_item_types(project_id: int | None = None, refresh: bool = False) -> str:
    """List Jama item types with their fields.

    With project_id, only the types present in that project, each with an item
    count — what /bc:setup uses to decide which types are requirements and to
    find the status, rationale, and GitHub-link fields.

    Args:
        project_id: Optional Jama project id to count items in.
        refresh: Ignore the 10-minute cache and re-read Jama.
    """
    types = await _item_types(refresh)
    counts: Counter | None = None
    if project_id is not None:
        try:
            snapshot, _ = await _project_snapshot(project_id, refresh)
        except SnapshotLoading as loading:
            return _dump(loading.payload())
        counts = Counter(item.get("itemType") for item in snapshot.items.values())
    listed = []
    for type_id, item_type in types.items():
        if counts is not None and not counts.get(type_id):
            continue
        entry: dict[str, Any] = {
            "id": type_id,
            "key": item_type.get("typeKey"),
            "display": _type_display(item_type),
            "kind": _container_kind(item_type) or "item",
            "fields": [
                {"label": f.get("label"), "name": f.get("name"), "type": f.get("fieldType")}
                for f in item_type.get("fields") or []
                if isinstance(f, dict)
            ],
        }
        if counts is not None:
            entry["count"] = counts[type_id]
        listed.append(entry)
    listed.sort(key=lambda e: (-e.get("count", 0), _norm(e["display"])))
    return _dump({"project_id": project_id, "item_types": listed})


@mcp.tool(annotations=_READ_ONLY, structured_output=False)
async def jama_get_features(
    project_id: int,
    feature_item_type: str = "Folder",
    item_type_exclude: list[str] | None = None,
    item_type_filter: list[str] | None = None,
    root_ids: list[int] | None = None,
    feature_depth: int = 1,
    exclude_ids: list[int] | None = None,
    refresh: bool = False,
) -> str:
    """List a Jama project's features with their paths and requirement counts.

    Without root_ids, every folder is a feature: `requirements` counts everything
    under it, sub-folders included (what a feature-scoped jama_get_requirements
    returns), `direct_requirements` those whose nearest folder it is. Requirements
    in no folder are summarized under `outside_features` by nearest Set/Component.

    With root_ids (the requirements roots from config), only what sits inside them
    counts, and the features are the containers `feature_depth` levels below a
    root — 1 means a root's top-level folders, with deeper folders rolled up into
    them. Only features that hold at least one requirement are listed.

    Args:
        project_id: Jama project id.
        feature_item_type: Item type that marks a feature when no roots are given. Default "Folder".
        item_type_exclude: Item types not counted as requirements (e.g. ["Text"]).
        item_type_filter: If set, count only these item types.
        root_ids: Containers (sets or folders) whose contents are the requirements in scope.
        feature_depth: Levels below a root that define a feature. Default 1.
        exclude_ids: Containers inside a root to leave out entirely (an archive folder, say).
        refresh: Ignore the 10-minute project cache and re-read Jama.
    """
    try:
        snapshot, cache_hit = await _project_snapshot(project_id, refresh)
    except SnapshotLoading as loading:
        return _dump(loading.payload())
    types = await _item_types(refresh)
    tree = Tree(snapshot.items, types, feature_item_type, root_ids, feature_depth, exclude_ids)
    selection = _select_requirements(tree, item_type_exclude or [], item_type_filter or [])
    source, warnings = _snapshot_notes(snapshot, tree, feature_item_type, cache_hit)

    if tree.level_mode:
        counts = Counter(tree.feature_of(iid) for iid in selection.ids)
        tree.assign_labels(set(counts))
        folders: dict[int | None, set[int | None]] = {}
        for iid in selection.ids:
            folders.setdefault(tree.feature_of(iid), set()).add(tree.nearest_container(iid))
        ordered = [fid for fid in tree.order if fid in counts] + ([None] if None in counts else [])
        features = [
            {
                "name": tree.label(fid),
                "path": tree.path(fid) if fid is not None else "",
                "jama_id": fid,
                "document_key": _document_key(tree.items[fid]) if fid is not None else "",
                "requirements": counts[fid],
                "folders": len(folders[fid]),
            }
            for fid in ordered
        ]
        roots = [{"id": root, "path": tree.path(root)} for root in tree.roots]
        summary = (
            f"{len(features)} features ({feature_depth} level{'s' if feature_depth != 1 else ''} below "
            f"{'; '.join(r['path'] for r in roots) or 'no valid root'}) holding {len(selection.ids)} requirements; "
            f"{selection.outside} items outside the requirements root ignored; {source}"
        )
        return _dump(
            {
                "project_id": project_id,
                "mode": "roots",
                "roots": roots,
                "feature_depth": feature_depth,
                "features": features,
                "summary": summary,
                "warnings": warnings,
                "fetched_at": snapshot.fetched_at,
                "truncated": snapshot.truncated,
            }
        )

    subtree: Counter = Counter()
    direct: Counter = Counter()
    outside: Counter = Counter()
    for iid in selection.ids:
        nearest = tree.feature_of(iid)
        if nearest is not None and tree.kind(nearest) == "feature":
            direct[nearest] += 1
        else:
            outside[nearest] += 1
        for ancestor in tree.ancestors(iid):
            if tree.kind(ancestor) == "feature":
                subtree[ancestor] += 1
    features = [
        {
            "name": tree.name(fid),
            "path": tree.path(fid),
            "jama_id": fid,
            "document_key": _document_key(tree.items[fid]),
            "depth": sum(1 for a in tree.ancestors(fid) if tree.kind(a) == "feature"),
            "requirements": subtree[fid],
            "direct_requirements": direct[fid],
        }
        for fid in tree.feature_ids()
    ]
    outside_features = [
        {
            "name": tree.name(cid) if cid is not None else _UNKNOWN_FEATURE,
            "path": tree.path(cid) if cid is not None else "",
            "requirements": count,
        }
        for cid, count in outside.items()
    ]
    summary = (
        f"{len(features)} features ({feature_item_type}) holding {sum(direct.values())} of "
        f"{len(selection.ids)} requirements; {sum(outside.values())} outside any feature; {source}"
    )
    return _dump(
        {
            "project_id": project_id,
            "mode": "nearest",
            "feature_item_type": feature_item_type,
            "features": features,
            "outside_features": outside_features,
            "summary": summary,
            "warnings": warnings,
            "fetched_at": snapshot.fetched_at,
            "truncated": snapshot.truncated,
        }
    )


@mcp.tool(annotations=_READ_ONLY, structured_output=False)
async def jama_get_requirements(
    project_id: int,
    scope: str = "",
    feature_item_type: str = "Folder",
    item_type_exclude: list[str] | None = None,
    item_type_filter: list[str] | None = None,
    field_mapping: dict[str, str] | None = None,
    include_custom_fields: bool = True,
    root_ids: list[int] | None = None,
    feature_depth: int = 1,
    exclude_ids: list[int] | None = None,
    refresh: bool = False,
    offset: int = 0,
) -> str:
    """Fetch a Jama project's requirements in the plugin's canonical schema, optionally scoped to one feature.

    Without root_ids, features are folders: each requirement's `feature` is its
    nearest Folder ancestor (or `feature_item_type`), falling back to the nearest
    Set/Component. With root_ids, only requirements inside those roots are in scope,
    and each one's `feature` is the container `feature_depth` levels below its root —
    deeper folders roll up into it, and `raw_fields.folder_path` keeps the exact
    location. Folders, sets, and components themselves are never returned.

    Large results come in pages sized to fit an MCP response. While the result
    carries `next_offset`, call again with the same arguments and
    `offset=next_offset`, and concatenate `requirements`. Later pages are served
    from the cached project tree, so they cost no extra Jama requests.

    Args:
        project_id: Jama project id.
        scope: "" or "all" for everything; a document key such as "CWA-REQ-85" for
            that requirement plus the rest of its feature; or a feature (folder)
            name or path such as "Patient Monitoring" or "Software/Patient Monitoring",
            which returns everything under that folder, sub-folders included.
        feature_item_type: Item type that marks a feature. Default "Folder".
        item_type_exclude: Item types to drop, by display name or type key (e.g. ["Text"]).
        item_type_filter: If set, keep only these item types.
        field_mapping: Field labels for "status", "rationale", and "github_url"
            (defaults "status", "rationale", none). github_url fills `_enrichment_urls`.
        include_custom_fields: Copy the remaining custom fields into raw_fields.
        root_ids: Requirements roots (sets or folders) from config; everything else is out of scope.
        feature_depth: With root_ids, the levels below a root that define a feature. Default 1.
        exclude_ids: With or without roots, containers whose contents are left out entirely.
        refresh: Ignore the 10-minute project cache and re-read Jama. Applies to the
            first page; later pages are served from the tree it read.
        offset: Position to resume from — the previous page's `next_offset`.

    A large project takes Jama a minute or more to send. If the read is still
    running when this call has to answer, the result is {loading: true, ...}
    with progress: call again with the same arguments to get the requirements.

    Returns JSON {project_id, scope: {input, kind, matched}, total, offset,
    next_offset, requirements, summary, warnings, fetched_at, truncated}.
    """
    refresh = refresh and offset <= 0
    try:
        snapshot, cache_hit = await _project_snapshot(project_id, refresh)
    except SnapshotLoading as loading:
        return _dump(loading.payload())
    types = await _item_types(refresh)
    tree = Tree(snapshot.items, types, feature_item_type, root_ids, feature_depth, exclude_ids)
    selection = _select_requirements(tree, item_type_exclude or [], item_type_filter or [])
    tree.assign_labels({tree.feature_of(iid) for iid in selection.ids})
    scoped = _apply_scope(tree, selection.ids, scope)
    mapping, ignored = _field_mapping(field_mapping)
    start = min(max(offset, 0), len(scoped.ids))
    remaining = scoped.ids[start:]
    await _load_options(_lookup_ids([tree.items[iid] for iid in remaining], types))
    base_url = _client().config.base_url
    requirements: list[dict[str, Any]] = []
    used = 0
    for iid in remaining:
        requirement = _render_requirement(tree, iid, mapping, include_custom_fields, base_url, project_id)
        size = len(_dump(requirement))
        if requirements and used + size > _PAGE_CHAR_BUDGET:
            break
        requirements.append(requirement)
        used += size
    end = start + len(requirements)
    next_offset = end if end < len(scoped.ids) else None

    source, warnings = _snapshot_notes(snapshot, tree, feature_item_type, cache_hit)
    if scoped.warning:
        warnings.append(scoped.warning)
    if ignored:
        warnings.append(f"Ignored field_mapping keys {ignored}; only {list(_MAPPABLE_FIELDS)} can be mapped.")
    feature_count = len({tree.feature_of(iid) for iid in selection.ids} - {None})
    parts = [f"{len(selection.ids)} requirements across {feature_count} features"]
    if selection.excluded:
        parts.append(f"{selection.excluded} items skipped by item_type_exclude")
    if selection.filtered:
        parts.append(f"{selection.filtered} items dropped by item_type_filter")
    if selection.outside:
        parts.append(f"{selection.outside} items outside the requirements root ignored")
    if scoped.kind != "all":
        matched = f": {'; '.join(scoped.matched)}" if scoped.matched else ""
        parts.append(f"scope '{scope.strip()}' ({scoped.kind}{matched}) -> {len(scoped.ids)} matched")
    if start or next_offset is not None:
        resume = f"; call again with offset={next_offset}" if next_offset is not None else "; last page"
        parts.append(f"page {start + 1}-{end} of {len(scoped.ids)}{resume}")
    parts.append(source)
    return _dump(
        {
            "project_id": project_id,
            "scope": {"input": scope, "kind": scoped.kind, "matched": scoped.matched},
            "total": len(scoped.ids),
            "offset": start,
            "next_offset": next_offset,
            "requirements": requirements,
            "summary": "; ".join(parts),
            "warnings": warnings,
            "fetched_at": snapshot.fetched_at,
            "truncated": snapshot.truncated,
        }
    )


@mcp.tool(annotations=_READ_ONLY, structured_output=False)
async def jama_get_item(
    item: str,
    project_id: int | None = None,
    feature_item_type: str = "Folder",
    field_mapping: dict[str, str] | None = None,
    root_ids: list[int] | None = None,
    feature_depth: int = 1,
) -> str:
    """Fetch one Jama item by document key (e.g. "CWA-REQ-85") or numeric id, with its traceability.

    For more than one item, call jama_get_items once with every key instead —
    it is much faster than calling this tool per key.

    Returns JSON {requirement, relationships: {upstream, downstream}, warnings}.
    `requirement` is in the canonical schema with `linked_ids` set to the document
    keys of every related item; each relationship carries the related item's key,
    title, relationship type, and suspect flag.

    Args:
        item: Document key or numeric Jama item id.
        project_id: Optional project id, to narrow a document-key lookup.
        feature_item_type: Item type that marks a feature. Default "Folder".
        field_mapping: Same as jama_get_requirements.
        root_ids, feature_depth: Same as jama_get_requirements, so the item's `feature` matches.
    """
    text = str(item or "").strip()
    if not text:
        raise JamaError("Pass a document key such as CWA-REQ-85, or a numeric Jama item id.")
    mapping, ignored = _field_mapping(field_mapping)
    batch = _ItemBatch()
    await _read_window([text], project_id, True, False, batch)
    loaded = batch.loaded[0]
    if loaded.error is not None:
        raise loaded.error
    if loaded.item_id is None:
        scope = f" in project {project_id}" if project_id is not None else ""
        what = f"id {text}" if text.isdigit() else f"document key '{text}'"
        raise JamaError(f"No Jama item has {what}{scope}.", status=404)
    [(_, entry)] = _render_batch(batch, feature_item_type, root_ids, feature_depth, mapping, True, True, project_id)
    assert entry is not None
    warnings = entry.get("warnings", []) + batch.notes
    if ignored:
        warnings.append(f"Ignored field_mapping keys {ignored}; only {list(_MAPPABLE_FIELDS)} can be mapped.")
    return _dump({"requirement": entry["requirement"], "relationships": entry["relationships"], "warnings": warnings})


@mcp.tool(annotations=_READ_ONLY, structured_output=False)
async def jama_get_items(
    items: list[str] | str,
    project_id: int | None = None,
    feature_item_type: str = "Folder",
    field_mapping: dict[str, str] | None = None,
    include_relationships: bool = True,
    include_custom_fields: bool = True,
    root_ids: list[int] | None = None,
    feature_depth: int = 1,
    refresh: bool = False,
    offset: int = 0,
) -> str:
    """Fetch many Jama items in one call by document key (e.g. "CWA-REQ-85") or numeric id, with their traceability.

    The fast way to read a known list of requirements: one call replaces a
    jama_get_item call per key, and it never reads the whole project. Keys are
    searched in batches, parent folders shared between items are read once, the
    requests run in parallel, and everything read is cached for 10 minutes.
    Each entry has the same shape as a jama_get_item result, in the requested order.

    Large results come in pages. While the result carries `next_offset`, call
    again with the same arguments and `offset=next_offset`, and concatenate
    `items`, `not_found`, and `failed`. Don't split a key list across parallel
    calls or sub-agents — the server already parallelises, so that only adds overhead.

    Args:
        items: Document keys or numeric Jama item ids, at most 500 — a list, or one
            string separated by commas or spaces.
        project_id: Optional project id, to narrow the document-key search.
        feature_item_type: Item type that marks a feature. Default "Folder".
        field_mapping: Same as jama_get_requirements.
        include_relationships: Read upstream/downstream traceability, which fills
            `linked_ids`. Default true; false is faster but leaves `linked_ids` empty.
        include_custom_fields: Copy the remaining custom fields into raw_fields.
            Default true; false gives much smaller results.
        root_ids, feature_depth: Same as jama_get_requirements, so each item's `feature` matches.
        refresh: Ignore the 10-minute cache and re-read the items and their relationships.
            Applies to the first page; later pages reuse what it read.
        offset: Position in `items` to resume from — the previous page's `next_offset`.

    Returns JSON {requested, offset, next_offset, items: [{requirement,
    relationships, warnings?}], not_found, failed: [{item, error}], summary, warnings}.
    """
    keys = _unique_keys(items)
    if not keys:
        raise JamaError("Pass at least one document key such as CWA-REQ-85, or a numeric Jama item id.")
    if len(keys) > _MAX_BATCH_ITEMS:
        raise JamaError(
            f"{len(keys)} items requested; pass at most {_MAX_BATCH_ITEMS} per call. To read a whole feature, use "
            "jama_get_requirements with scope instead."
        )
    start = min(max(offset, 0), len(keys))
    refresh = refresh and start == 0
    mapping, ignored = _field_mapping(field_mapping)
    client = _client()
    before = client.requests
    batch = _ItemBatch()

    def render() -> list[tuple[_Loaded, dict[str, Any] | None]]:
        return _render_batch(
            batch, feature_item_type, root_ids, feature_depth, mapping,
            include_custom_fields, include_relationships, project_id,
        )

    # Read a window at a time until the page is full, or until another window like the last one would overrun
    # the time budget — so every call answers well inside a client's timeout. Whatever a window read past the
    # page is cached for the next call.
    deadline = time.monotonic() + _BATCH_TIME_BUDGET_SECONDS
    index = start
    while index < len(keys):
        window = keys[index : index + _BATCH_WINDOW]
        window_started = time.monotonic()
        await _read_window(window, project_id, include_relationships, refresh, batch)
        index += len(window)
        now = time.monotonic()
        if now + (now - window_started) > deadline or sum(len(_dump(e)) for _, e in render() if e) >= _PAGE_CHAR_BUDGET:
            break

    page: list[dict[str, Any]] = []
    not_found: list[str] = []
    failed: list[dict[str, str]] = []
    used = consumed = 0
    for loaded, entry in render():
        if entry is not None:
            size = len(_dump(entry))
            if page and used + size > _PAGE_CHAR_BUDGET:
                break
            page.append(entry)
            used += size
        elif loaded.error is not None:
            failed.append({"item": loaded.key, "error": str(loaded.error)})
        else:
            not_found.append(loaded.key)
        consumed += 1
    end = start + consumed
    next_offset = end if end < len(keys) else None

    parts = [f"{len(page)} of keys {start + 1}-{end} (of {len(keys)}) returned"]
    if not_found:
        parts.append(f"{len(not_found)} not found")
    if failed:
        parts.append(f"{len(failed)} failed — pass them again to retry")
    if not include_relationships:
        parts.append("relationships not read, so linked_ids is empty")
    parts.append(f"{client.requests - before} Jama requests")
    if next_offset is not None:
        parts.append(f"call again with offset={next_offset} for the rest")
    elif start:
        parts.append("last page")
    warnings = list(batch.notes)
    if ignored:
        warnings.append(f"Ignored field_mapping keys {ignored}; only {list(_MAPPABLE_FIELDS)} can be mapped.")
    return _dump(
        {
            "requested": len(keys),
            "offset": start,
            "next_offset": next_offset,
            "items": page,
            "not_found": not_found,
            "failed": failed,
            "summary": "; ".join(parts),
            "warnings": warnings,
        }
    )


# ---------------------------------------------------------------------------
# Scope proposal — anchor the requirements on what the test suite links to
# ---------------------------------------------------------------------------

_ROOT_DOMINANCE = 0.95  # a container holding this share of the links is descended into, or becomes the root
_ROOT_SIGNIFICANCE = 0.05  # below this share, links are outliers rather than a reason to widen the root
_MAX_ROOTS = 6
_MAX_ANCHORS = 5000
_MAX_PREVIEW_FEATURES = 200
_MAX_CANDIDATES = 12
_MAX_OUTLIERS_LISTED = 20


def _choose_roots(tree: Tree, counts: Counter, total: int) -> list[int]:
    """The smallest containers holding nearly all linked requirements.

    Walk down from the project while a single child container holds at least 95% of the links below the
    current node. A Set or Folder reached that way is the root: its top-level folders become the
    features. Above set level — the project itself, or a Component, which groups different documents —
    split instead into the few children that together hold 95% of the links, each walked down the same
    way. Links outside the chosen roots are outliers to report, never a reason to widen a root.
    """

    def linked_children(node: int | None) -> list[int]:
        return sorted(
            (c for c in tree.children.get(node, []) if tree.is_container(c) and counts[c]),
            key=lambda c: (-counts[c], _order_key(tree.items[c])),
        )

    def resolve(node: int | None, linked: int) -> list[int]:
        children = linked_children(node)
        while children and counts[children[0]] >= _ROOT_DOMINANCE * linked:
            node, linked = children[0], counts[children[0]]
            children = linked_children(node)
        if node is not None and tree.kind(node) != "component":
            return [node]
        significant = [c for c in children if counts[c] >= _ROOT_SIGNIFICANCE * linked]
        if (
            significant
            and len(significant) <= _MAX_ROOTS
            and sum(counts[c] for c in significant) >= _ROOT_DOMINANCE * linked
        ):
            return [root for child in significant for root in resolve(child, counts[child])]
        return [] if node is None else [node]

    return resolve(None, total) if total else []


def _parse_keys(values: list[str] | str) -> list[str]:
    raw = values if isinstance(values, list) else [values]
    keys: list[str] = []
    for value in raw:
        keys.extend(token for token in re.split(r"[\s,;]+", str(value or "")) if token)
    return list(dict.fromkeys(keys))


def _scope_proposal(
    snapshot: ProjectSnapshot,
    types: dict[int, dict[str, Any]],
    document_keys: list[str] | str,
    feature_depth: int = 1,
    item_type_exclude: list[str] | None = None,
    root_ids: list[int] | None = None,
    exclude_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Where the requirements a test suite links to live, and the root and features that follow."""
    tree = Tree(snapshot.items, types)
    by_key: dict[str, int] = {}
    for iid, item in snapshot.items.items():
        key = _document_key(item)
        if key and not tree.is_container(iid):
            by_key.setdefault(key.casefold(), iid)
    keys = _parse_keys(document_keys)
    warnings: list[str] = []
    if len(keys) > _MAX_ANCHORS:
        warnings.append(f"Used the first {_MAX_ANCHORS} of {len(keys)} linked IDs.")
        keys = keys[:_MAX_ANCHORS]
    resolved = {key: by_key[key.casefold()] for key in keys if key.casefold() in by_key}
    not_found = [key for key in keys if key.casefold() not in by_key]
    linked = list(dict.fromkeys(resolved.values()))
    if snapshot.truncated:
        warnings.append(f"Only the first {len(snapshot.items)} of {snapshot.total} items were read; the proposal may be off.")
    if keys and not linked:
        warnings.append(
            f"None of the {len(keys)} linked IDs exist in project {snapshot.project_id}. Check that this is the "
            "Jama project your test cases link to, and that the requirement field holds Jama document keys."
        )

    type_counts = Counter(tree.items[iid].get("itemType") for iid in linked)
    threshold = max(1, math.ceil(_ROOT_SIGNIFICANCE * len(linked)))
    item_types = [
        {
            "display": _type_display(types.get(type_id), type_id),
            "key": (types.get(type_id) or {}).get("typeKey"),
            "linked": count,
            "proposed": count >= threshold,
        }
        for type_id, count in type_counts.most_common()
    ]
    proposed_types = [entry["display"] for entry in item_types if entry["proposed"]]

    counts: Counter = Counter()
    for iid in linked:
        for ancestor in tree.ancestors(iid):
            if tree.is_container(ancestor):
                counts[ancestor] += 1
    roots = [int(r) for r in root_ids] if root_ids else _choose_roots(tree, counts, len(linked))
    if linked and not roots:
        warnings.append(
            "The linked requirements are spread across the whole project with no narrower container holding "
            "most of them, so no root is proposed. Pick one from `candidates`, or keep the whole project."
        )

    candidates = sorted(
        (c for c in counts if counts[c] >= threshold),
        key=lambda c: (-counts[c], -len(tree.ancestors(c)), _order_key(tree.items[c])),
    )[:_MAX_CANDIDATES]

    result: dict[str, Any] = {
        "project_id": snapshot.project_id,
        "anchors": {
            "given": len(keys),
            "resolved": len(linked),
            "not_found": len(not_found),
            "not_found_sample": not_found[:_MAX_OUTLIERS_LISTED],
        },
        "item_types": item_types,
        "proposed_item_types": proposed_types,
        "candidates": [
            {"id": c, "path": tree.path(c), "kind": tree.kind(c), "linked": counts[c]} for c in candidates
        ],
        "feature_depth": feature_depth,
        "roots": [],
        "coverage": 0.0,
        "outliers": {"count": 0, "sample": []},
        "features": {"count": 0, "list": [], "without_links": []},
        "requirements": {"in_roots": 0, "of_type_outside_roots": 0},
        "warnings": warnings,
        "fetched_at": snapshot.fetched_at,
    }
    if not roots:
        result["summary"] = (
            f"{len(linked)} of {len(keys)} linked IDs found in project {snapshot.project_id}; no root proposed."
        )
        return result

    level = Tree(snapshot.items, types, root_ids=roots, feature_depth=feature_depth, exclude_ids=exclude_ids)
    warnings.extend(f"Root {r} is not a container in this project." for r in level.missing_roots)
    covered = [iid for iid in linked if level.in_scope(iid)]
    outliers = [iid for iid in linked if not level.in_scope(iid)]
    key_of = {iid: key for key, iid in resolved.items()}
    selection = _select_requirements(level, item_type_exclude or [], proposed_types)
    all_of_type = _select_requirements(tree, item_type_exclude or [], proposed_types)
    per_feature = Counter(level.feature_of(iid) for iid in selection.ids)
    linked_per_feature = Counter(level.feature_of(iid) for iid in covered)
    level.assign_labels(set(per_feature))
    ordered = [fid for fid in level.order if fid in per_feature]
    features = [
        {
            "name": level.label(fid),
            "path": level.path(fid),
            "jama_id": fid,
            "requirements": per_feature[fid],
            "linked": linked_per_feature[fid],
        }
        for fid in ordered
    ]
    coverage = round(len(covered) / len(linked), 3) if linked else 0.0
    result.update(
        {
            "roots": [
                {"id": r, "path": level.path(r), "kind": level.kind(r), "linked": counts.get(r, 0)} for r in level.roots
            ],
            "coverage": coverage,
            "outliers": {
                "count": len(outliers),
                "sample": [
                    {
                        "key": key_of.get(iid, str(iid)),
                        "path": tree.path(tree.nearest_container(iid)) if tree.nearest_container(iid) is not None else "",
                    }
                    for iid in outliers[:_MAX_OUTLIERS_LISTED]
                ],
            },
            "features": {
                "count": len(features),
                "list": features[:_MAX_PREVIEW_FEATURES],
                "without_links": [f["name"] for f in features if not f["linked"]],
            },
            "requirements": {
                "in_roots": len(selection.ids),
                "of_type_outside_roots": len(all_of_type.ids) - len(selection.ids),
            },
        }
    )
    root_paths = "; ".join(r["path"] for r in result["roots"])
    types_text = ", ".join(proposed_types) or "no item type"
    without_links = len(result["features"]["without_links"])
    result["summary"] = (
        f"{len(linked)} of {len(keys)} linked IDs found ({types_text}); root {root_paths} holds "
        f"{round(coverage * 100)}% of them; {len(features)} features {feature_depth} level"
        f"{'s' if feature_depth != 1 else ''} below it with {len(selection.ids)} requirements"
        + (f"; {without_links} of the {len(features)} features have no linked case" if without_links else "")
        + "."
    )
    return result


@mcp.tool(annotations=_READ_ONLY, structured_output=False)
async def jama_get_scope_proposal(
    project_id: int,
    document_keys: list[str],
    feature_depth: int = 1,
    item_type_exclude: list[str] | None = None,
    root_ids: list[int] | None = None,
    exclude_ids: list[int] | None = None,
    refresh: bool = False,
) -> str:
    """Propose the requirements root for a project from the requirement IDs its test cases link to.

    Finds where those requirements live in the project tree, proposes the smallest
    container(s) holding nearly all of them as the root, the item types they belong
    to, and previews the features (the containers `feature_depth` levels below the
    root) with their requirement counts and how many linked IDs fall in each. A few
    links elsewhere are reported as outliers instead of widening the root. Features
    with no linked IDs are listed: they are untested requirements — or an archive to
    exclude, which only a person can tell. Pass root_ids / exclude_ids to preview an
    adjustment instead of the computed root.

    Args:
        project_id: Jama project id.
        document_keys: Requirement IDs the test cases link to (a spread sample is enough).
        feature_depth: Levels below the root that define a feature. Default 1.
        item_type_exclude: Item types never counted as requirements.
        root_ids: Preview these roots instead of proposing one.
        exclude_ids: Containers inside the root to leave out.
        refresh: Ignore the 10-minute project cache and re-read Jama.
    """
    try:
        snapshot, _ = await _project_snapshot(project_id, refresh)
    except SnapshotLoading as loading:
        return _dump(loading.payload())
    types = await _item_types(refresh)
    return _dump(
        _scope_proposal(snapshot, types, document_keys, feature_depth, item_type_exclude, root_ids, exclude_ids)
    )


# ---------------------------------------------------------------------------
# One-shot connection check for /bc:setup (runs before the server is registered)
# ---------------------------------------------------------------------------


def _field_candidates(tree: Tree, ids: list[int]) -> dict[str, Any]:
    """Fields that look like status, rationale, or a GitHub doc link, for setup's mapping guess."""
    found: dict[str, list[str]] = {"status": [], "rationale": [], "github_url": []}

    def add(kind: str, label: str) -> None:
        if label and label not in found[kind]:
            found[kind].append(label)

    used_types = Counter(tree.items[iid].get("itemType") for iid in ids)
    for type_id, _ in used_types.most_common():
        for field_def in (tree.types.get(type_id) or {}).get("fields") or []:
            if not isinstance(field_def, dict):
                continue
            label = str(field_def.get("label") or field_def.get("name") or "")
            name = _norm(str(field_def.get("name") or "").split("$", 1)[0])
            text = f"{_norm(label)} {name}"
            if "rationale" in text:
                add("rationale", label)
            if _norm(label) == "status" or name == "status":
                add("status", label)
            if "github" in text.replace(" ", ""):
                add("github_url", label)

    for iid in ids[:500]:  # fields that hold GitHub links without saying so in their label
        item = tree.items[iid]
        defs = _field_defs(tree.types.get(item.get("itemType")))
        for key, value in (item.get("fields") or {}).items():
            if key != "description" and isinstance(value, str) and _GITHUB_URL_RE.search(value):
                add("github_url", str((_def_for(defs, key) or {}).get("label") or key))

    github: list[dict[str, Any]] = []
    for label in found["github_url"]:
        first_url = None
        for iid in ids:
            item = tree.items[iid]
            fields = item.get("fields") or {}
            key = _field_key(fields, _field_defs(tree.types.get(item.get("itemType"))), label)
            urls = _github_urls(fields.get(key)) if key else []
            if urls:
                first_url = urls[0]
                break
        github.append({"field": label, "first_url": first_url})
    return {"status": found["status"], "rationale": found["rationale"], "github_url": github}


async def _check(
    project_id: int | None,
    transport: httpx.AsyncBaseTransport | None = None,
    anchors: list[str] | None = None,
    feature_depth: int = 1,
    root_ids: list[int] | None = None,
    exclude_ids: list[int] | None = None,
    item_type_exclude: list[str] | None = None,
) -> dict[str, Any]:
    config = load_config()
    reset_state(JamaClient(config, transport=transport))
    client = _client()
    try:
        user = _user_summary((await client.get("users/current")).get("data") or {})
        report: dict[str, Any] = {"ok": True, "base_url": config.base_url, "auth": config.auth_mode, "user": user}
        if project_id is None:
            projects = await _list_projects()
            report["projects"] = projects
            report["summary"] = (
                f"Connected to {config.base_url} as {user['name']} ({config.auth_mode}); "
                f"{len(projects)} readable projects."
            )
            return report

        project = (await client.get(f"projects/{project_id}")).get("data") or {}
        snapshot, _ = await _project_snapshot(project_id, refresh=True, background=False)
        types = await _item_types(refresh=True)
        tree = Tree(snapshot.items, types, "Folder")
        selection = _select_requirements(tree, [], [])
        features = tree.feature_ids()
        outside = sum(
            1 for iid in selection.ids if tree.feature_of(iid) is None or tree.kind(tree.feature_of(iid)) != "feature"
        )
        samples: dict[str, list[str]] = {}
        for iid in selection.ids:
            item = tree.items[iid]
            bucket = samples.setdefault(_type_display(types.get(item.get("itemType")), item.get("itemType")), [])
            if len(bucket) < 5 and _document_key(item):
                bucket.append(_document_key(item))
        counts = Counter(item.get("itemType") for item in snapshot.items.values())
        project_fields = project.get("fields") or {}
        project_key = project.get("projectKey") or project_fields.get("projectKey")
        report.update(
            {
                "project": {"id": project_id, "key": project_key, "name": project_fields.get("name") or project.get("name")},
                "items": len(snapshot.items),
                "requests": snapshot.requests,
                "truncated": snapshot.truncated,
                "item_types": [
                    {
                        "id": type_id,
                        "key": (types.get(type_id) or {}).get("typeKey"),
                        "display": _type_display(types.get(type_id), type_id),
                        "kind": (_container_kind(types[type_id]) if type_id in types else None) or "item",
                        "count": count,
                    }
                    for type_id, count in counts.most_common()
                ],
                "features": {"count": len(features), "sample": [tree.path(fid) for fid in features[:20]]},
                "requirements_outside_features": outside,
                "document_key_samples": samples,
                "field_candidates": _field_candidates(tree, selection.ids),
                "summary": (
                    f"Connected as {user['name']}; project {project_key or project_id} has {len(snapshot.items)} items "
                    f"in {len(features)} folders (features), {len(selection.ids)} of them non-container items "
                    f"({snapshot.requests} requests)."
                ),
            }
        )
        if anchors is not None:
            report["scope_proposal"] = _scope_proposal(
                snapshot, types, anchors, feature_depth, item_type_exclude, root_ids, exclude_ids
            )
        return report
    finally:
        await client.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Jama MCP server. Without --check it serves MCP over stdio.")
    parser.add_argument("--check", action="store_true", help="test the connection, print a JSON report, and exit")
    parser.add_argument("--project", type=int, help="with --check: also inspect this project's item types and folders")
    parser.add_argument(
        "--anchors-file",
        help="with --project: a file of requirement IDs the test cases link to; adds a scope_proposal to the report",
    )
    parser.add_argument("--feature-depth", type=int, default=1, help="levels below the root that define a feature")
    parser.add_argument("--root", type=int, action="append", help="preview this root instead of proposing one")
    parser.add_argument("--exclude", type=int, action="append", help="container inside the root to leave out")
    parser.add_argument("--item-type-exclude", action="append", help="item type never counted as a requirement")
    args = parser.parse_args(argv)
    if not args.check:
        if args.project is not None or args.anchors_file:
            parser.error("--project and --anchors-file only apply together with --check")
        mcp.run()
        return 0
    if args.anchors_file and args.project is None:
        parser.error("--anchors-file needs --project")
    try:
        anchors = None
        if args.anchors_file:
            try:
                with open(args.anchors_file, encoding="utf-8") as handle:
                    anchors = _parse_keys(handle.read())
            except OSError as exc:
                raise JamaError(f"Cannot read --anchors-file: {exc}") from None
        report = asyncio.run(
            _check(
                args.project,
                anchors=anchors,
                feature_depth=args.feature_depth,
                root_ids=args.root,
                exclude_ids=args.exclude,
                item_type_exclude=args.item_type_exclude,
            )
        )
    except JamaError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, ensure_ascii=False))
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
