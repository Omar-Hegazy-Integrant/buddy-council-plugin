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
import os
import re
import sys
import time
import urllib.parse
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

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
_TOKEN_EXPIRY_MARGIN_SECONDS = 60.0
_RATE_LIMIT_STATUS = 429
_MAX_RATE_LIMIT_RETRIES = 3
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
        while True:
            kwargs: dict[str, Any] = {"params": query}
            if self.config.auth_mode == "oauth":
                kwargs["headers"] = {"Authorization": f"Bearer {await self._bearer()}"}
            else:
                kwargs["auth"] = (self.config.username, self.config.password)
            self.requests += 1
            try:
                response = await self._http.get(url, **kwargs)
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
        self, path: str, params: dict[str, Any] | None = None, max_items: int | None = None
    ) -> Collection:
        """GET every page of a listing (``startAt``/``maxResults``), merging ``linked`` objects."""
        limit = _MAX_ITEMS if max_items is None else max_items
        data: list[dict[str, Any]] = []
        linked: dict[str, dict[str, Any]] = {}
        start = 0
        while True:
            body = await self.get(path, {**(params or {}), "startAt": start, "maxResults": _PAGE_SIZE})
            chunk = body.get("data") or []
            if isinstance(chunk, dict):
                chunk = [chunk]
            data.extend(entry for entry in chunk if isinstance(entry, dict))
            for kind, objects in (body.get("linked") or {}).items():
                if isinstance(objects, dict):
                    linked.setdefault(kind, {}).update(objects)
            page = (body.get("meta") or {}).get("pageInfo") or {}
            count = int(page.get("resultCount", len(chunk)) or 0)
            total = int(page.get("totalResults", len(data)) or 0)
            start += count
            if count == 0 or not chunk or start >= total:
                return Collection(data, max(total, len(data)), False, linked)
            if len(data) >= limit:
                return Collection(data[:limit], total, True, linked)


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


class _State:
    def __init__(self, client: JamaClient | None = None):
        self.client = client
        self.item_types: dict[int, dict[str, Any]] | None = None
        self.item_types_loaded_at = 0.0
        self.options: dict[int, str] = {}
        self.loaded_picklists: set[int] = set()
        self.relationship_types: dict[int, str] | None = None
        self.projects: dict[int, ProjectSnapshot] = {}
        self.project_locks: dict[int, asyncio.Lock] = {}


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
    stale = time.monotonic() - state.item_types_loaded_at > _CACHE_TTL_SECONDS
    if state.item_types is None or refresh or stale:
        listing = await _client().get_all("itemtypes")
        state.item_types = {int(t["id"]): t for t in listing.data if t.get("id") is not None}
        state.item_types_loaded_at = time.monotonic()
    return state.item_types


async def _project_snapshot(project_id: int, refresh: bool = False) -> tuple[ProjectSnapshot, bool]:
    """Every item in the project tree, cached for _CACHE_TTL_SECONDS. Returns (snapshot, cache_hit)."""
    state = _STATE
    lock = state.project_locks.setdefault(project_id, asyncio.Lock())
    async with lock:
        cached = state.projects.get(project_id)
        if cached and not refresh and time.monotonic() - cached.loaded_at < _CACHE_TTL_SECONDS:
            return cached, True
        client = _client()
        before = client.requests
        listing = await client.get_all("items", {"project": project_id})
        snapshot = ProjectSnapshot(
            project_id=project_id,
            items={int(item["id"]): item for item in listing.data if item.get("id") is not None},
            total=listing.total,
            truncated=listing.truncated,
            requests=client.requests - before,
            loaded_at=time.monotonic(),
            fetched_at=_utc_now(),
        )
        state.projects[project_id] = snapshot
        return snapshot, False


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
    """The project tree rebuilt from each item's ``location.parent.item``."""

    def __init__(self, items: dict[int, dict[str, Any]], types: dict[int, dict[str, Any]], feature_item_type: str = "Folder"):
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
        """Nearest feature (folder) ancestor; else the nearest Set/Component; else None."""
        if iid not in self._feature_cache:
            chain = self.ancestors(iid)
            found = next((a for a in chain if self.kind(a) == "feature"), None)
            if found is None:
                found = next((a for a in chain if self.is_container(a)), None)
            self._feature_cache[iid] = found
        return self._feature_cache[iid]

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


def _select_requirements(tree: Tree, item_type_exclude: list[str], item_type_filter: list[str]) -> Selection:
    """Every non-container item, minus excluded types, limited to the filter when one is given."""
    exclude = {_norm(name) for name in item_type_exclude if _norm(name)}
    include = {_norm(name) for name in item_type_filter if _norm(name)}
    ids: list[int] = []
    excluded = filtered = 0
    for iid in tree.order:
        if tree.is_container(iid):
            continue
        item_type = tree.types.get(tree.items[iid].get("itemType"))
        if exclude and _type_matches(item_type, exclude):
            excluded += 1
            continue
        if include and not _type_matches(item_type, include):
            filtered += 1
            continue
        ids.append(iid)
    return Selection(ids, excluded, filtered)


@dataclass
class ScopeResult:
    ids: list[int]
    kind: str  # "all" | "requirement" | "feature" | "none"
    matched: list[str]
    warning: str | None


def _match_containers(tree: Tree, scope: str) -> list[int]:
    """Containers named ``scope`` — or, for "A/B" style input, whose path ends in those segments.

    Folders of the feature type win over Sets/Components with the same name.
    """
    target = _norm(scope)
    wanted_segments = _segments(scope)
    containers = [iid for iid in tree.order if tree.is_container(iid)]
    matches = [c for c in containers if _norm(tree.name(c)) == target]
    if not matches and len(wanted_segments) > 1:
        matches = [c for c in containers if _segments(tree.path(c))[-len(wanted_segments):] == wanted_segments]
    features = [c for c in matches if tree.kind(c) == "feature"]
    return features or matches


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

    containers = _match_containers(tree, text)
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
    """Resolve pick-list option ids to names: one call per pick list, per-option only as a fallback."""
    state = _STATE
    client = _client()
    for pick_list, option_ids in wanted.items():
        missing = {o for o in option_ids if o not in state.options}
        if not missing:
            continue
        if pick_list is not None and pick_list not in state.loaded_picklists:
            state.loaded_picklists.add(pick_list)
            try:
                listing = await client.get_all(f"picklists/{pick_list}/options")
            except JamaError as exc:
                if exc.status not in (403, 404):
                    raise
            else:
                for option in listing.data:
                    if option.get("id") is not None:
                        state.options[int(option["id"])] = str(option.get("name") or option.get("value") or option["id"])
            missing = {o for o in missing if o not in state.options}
        for option_id in sorted(missing):
            try:
                body = await client.get(f"picklistoptions/{option_id}")
            except JamaError as exc:
                if exc.status not in (403, 404):
                    raise
                state.options[option_id] = str(option_id)
                continue
            data = body.get("data") or {}
            state.options[option_id] = str(data.get("name") or data.get("value") or option_id)


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
        "feature": tree.name(feature) if feature is not None else _UNKNOWN_FEATURE,
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


async def _resolve_item_id(item: str, project_id: int | None) -> int:
    text = str(item or "").strip()
    if not text:
        raise JamaError("Pass a document key such as CWA-REQ-85, or a numeric Jama item id.")
    if text.isdigit():
        return int(text)
    client = _client()
    for candidate in dict.fromkeys((text, text.upper())):
        params: dict[str, Any] = {"documentKey": candidate, "maxResults": _PAGE_SIZE}
        if project_id is not None:
            params["project"] = project_id
        data = (await client.get("abstractitems", params)).get("data") or []
        if isinstance(data, dict):
            data = [data]
        match = next(
            (d for d in data if isinstance(d, dict) and _document_key(d).casefold() == text.casefold()),
            None,
        )
        if match and match.get("id") is not None:
            return int(match["id"])
    scope = f" in project {project_id}" if project_id is not None else ""
    raise JamaError(f"No Jama item has document key '{text}'{scope}.", status=404)


async def _add_ancestors(items: dict[int, dict[str, Any]], item_id: int) -> None:
    """Walk up from one item with GET /items/{parent} until the project root."""
    client = _client()
    current = items[item_id]
    for _ in range(_MAX_ANCESTOR_DEPTH):
        parent = ((current.get("location") or {}).get("parent") or {}).get("item")
        if not isinstance(parent, int) or parent in items:
            return
        current = (await client.get(f"items/{parent}")).get("data") or {}
        if not isinstance(current, dict) or current.get("id") is None:
            return
        items[int(current["id"])] = current


async def _relationships(item_id: int, direction: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Upstream or downstream traceability for one item, with the related items' document keys."""
    client = _client()
    path = f"items/{item_id}/{direction}relationships"
    try:
        listing = await client.get_all(path, {"include": ["data.fromItem", "data.toItem"]})
    except JamaError as exc:
        if exc.status != 400:
            raise
        listing = await client.get_all(path)  # an instance that rejects include= still answers plainly
    linked_items = listing.linked.get("items", {})
    other_side = "fromItem" if direction == "upstream" else "toItem"
    try:
        relationship_types = await _relationship_types()
    except JamaError as exc:
        if exc.status not in (403, 404):
            raise
        relationship_types = {}
    related: list[dict[str, Any]] = []
    lookups = unresolved = 0
    for relationship in listing.data:
        other = relationship.get(other_side)
        if not isinstance(other, int):
            continue
        info = linked_items.get(str(other)) or linked_items.get(other)
        if info is None:
            if lookups < _MAX_RELATED_LOOKUPS:
                lookups += 1
                try:
                    info = (await client.get(f"abstractitems/{other}")).get("data")
                except JamaError as exc:
                    if exc.status not in (403, 404):
                        raise
            else:
                unresolved += 1
        info = info if isinstance(info, dict) else {}
        type_id = relationship.get("relationshipType")
        related.append(
            {
                "id": _document_key(info) or str(other),
                "jama_id": other,
                "title": html_to_text((info.get("fields") or {}).get("name")),
                "relationship": relationship_types.get(type_id, str(type_id)) if type_id is not None else "",
                "suspect": bool(relationship.get("suspect")),
            }
        )
    warnings = []
    if unresolved:
        warnings.append(f"{unresolved} {direction} related items were left as Jama ids to bound the number of requests.")
    return related, warnings


# ---------------------------------------------------------------------------
# Tools — all read-only, all named jama_get_*
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READ_ONLY)
async def jama_get_current_user() -> str:
    """Check the Jama connection: the account this server authenticates as, and the auth method in use."""
    client = _client()
    user = (await client.get("users/current")).get("data") or {}
    return _dump({"base_url": client.config.base_url, "auth": client.config.auth_mode, "user": _user_summary(user)})


@mcp.tool(annotations=_READ_ONLY)
async def jama_get_projects() -> str:
    """List the Jama projects this account can read: id, key, and name (project folders omitted)."""
    return _dump({"projects": await _list_projects()})


@mcp.tool(annotations=_READ_ONLY)
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
        snapshot, _ = await _project_snapshot(project_id, refresh)
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


@mcp.tool(annotations=_READ_ONLY)
async def jama_get_features(
    project_id: int,
    feature_item_type: str = "Folder",
    item_type_exclude: list[str] | None = None,
    item_type_filter: list[str] | None = None,
    refresh: bool = False,
) -> str:
    """List a Jama project's features — its folders — with their paths and requirement counts.

    `requirements` counts everything under a folder, sub-folders included (what a
    feature-scoped jama_get_requirements returns); `direct_requirements` counts
    those whose nearest folder is this one (their `feature` field). Requirements
    in no folder are summarized under `outside_features` by nearest Set/Component.

    Args:
        project_id: Jama project id.
        feature_item_type: Item type that marks a feature. Default "Folder".
        item_type_exclude: Item types not counted as requirements (e.g. ["Text"]).
        item_type_filter: If set, count only these item types.
        refresh: Ignore the 10-minute project cache and re-read Jama.
    """
    snapshot, cache_hit = await _project_snapshot(project_id, refresh)
    types = await _item_types(refresh)
    tree = Tree(snapshot.items, types, feature_item_type)
    selection = _select_requirements(tree, item_type_exclude or [], item_type_filter or [])
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
    source, warnings = _snapshot_notes(snapshot, tree, feature_item_type, cache_hit)
    summary = (
        f"{len(features)} features ({feature_item_type}) holding {sum(direct.values())} of "
        f"{len(selection.ids)} requirements; {sum(outside.values())} outside any feature; {source}"
    )
    return _dump(
        {
            "project_id": project_id,
            "feature_item_type": feature_item_type,
            "features": features,
            "outside_features": outside_features,
            "summary": summary,
            "warnings": warnings,
            "fetched_at": snapshot.fetched_at,
            "truncated": snapshot.truncated,
        }
    )


@mcp.tool(annotations=_READ_ONLY)
async def jama_get_requirements(
    project_id: int,
    scope: str = "",
    feature_item_type: str = "Folder",
    item_type_exclude: list[str] | None = None,
    item_type_filter: list[str] | None = None,
    field_mapping: dict[str, str] | None = None,
    include_custom_fields: bool = True,
    refresh: bool = False,
    offset: int = 0,
) -> str:
    """Fetch a Jama project's requirements in the plugin's canonical schema, optionally scoped to one feature.

    Features are folders: each requirement's `feature` is its nearest Folder
    ancestor (or `feature_item_type`), falling back to the nearest Set/Component.
    Folders, sets, and components themselves are never returned as requirements.

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
        refresh: Ignore the 10-minute project cache and re-read Jama.
        offset: Position to resume from — the previous page's `next_offset`.

    Returns JSON {project_id, scope: {input, kind, matched}, total, offset,
    next_offset, requirements, summary, warnings, fetched_at, truncated}.
    """
    snapshot, cache_hit = await _project_snapshot(project_id, refresh)
    types = await _item_types(refresh)
    tree = Tree(snapshot.items, types, feature_item_type)
    selection = _select_requirements(tree, item_type_exclude or [], item_type_filter or [])
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


@mcp.tool(annotations=_READ_ONLY)
async def jama_get_item(
    item: str,
    project_id: int | None = None,
    feature_item_type: str = "Folder",
    field_mapping: dict[str, str] | None = None,
) -> str:
    """Fetch one Jama item by document key (e.g. "CWA-REQ-85") or numeric id, with its traceability.

    Returns JSON {requirement, relationships: {upstream, downstream}, warnings}.
    `requirement` is in the canonical schema with `linked_ids` set to the document
    keys of every related item; each relationship carries the related item's key,
    title, relationship type, and suspect flag.

    Args:
        item: Document key or numeric Jama item id.
        project_id: Optional project id, to narrow a document-key lookup.
        feature_item_type: Item type that marks a feature. Default "Folder".
        field_mapping: Same as jama_get_requirements.
    """
    client = _client()
    item_id = await _resolve_item_id(item, project_id)
    data = (await client.get(f"items/{item_id}")).get("data") or {}
    if not isinstance(data, dict) or data.get("id") is None:
        raise JamaError(f"Jama returned no item for id {item_id}.", status=404)
    project = data.get("project") or project_id
    cached = _STATE.projects.get(project) if isinstance(project, int) else None
    if cached and time.monotonic() - cached.loaded_at < _CACHE_TTL_SECONDS:
        items = {**cached.items, item_id: data}
    else:
        items = {item_id: data}
        await _add_ancestors(items, item_id)
    types = await _item_types()
    tree = Tree(items, types, feature_item_type)
    mapping, ignored = _field_mapping(field_mapping)
    await _load_options(_lookup_ids([data], types))
    requirement = _render_requirement(tree, item_id, mapping, True, client.config.base_url, project)

    upstream, upstream_warnings = await _relationships(item_id, "upstream")
    downstream, downstream_warnings = await _relationships(item_id, "downstream")
    requirement["linked_ids"] = list(dict.fromkeys(rel["id"] for rel in upstream + downstream))
    warnings = upstream_warnings + downstream_warnings
    if ignored:
        warnings.append(f"Ignored field_mapping keys {ignored}; only {list(_MAPPABLE_FIELDS)} can be mapped.")
    return _dump(
        {
            "requirement": requirement,
            "relationships": {"upstream": upstream, "downstream": downstream},
            "warnings": warnings,
        }
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


async def _check(project_id: int | None, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
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
        snapshot, _ = await _project_snapshot(project_id, refresh=True)
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
        return report
    finally:
        await client.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Jama MCP server. Without --check it serves MCP over stdio.")
    parser.add_argument("--check", action="store_true", help="test the connection, print a JSON report, and exit")
    parser.add_argument("--project", type=int, help="with --check: also inspect this project's item types and folders")
    args = parser.parse_args(argv)
    if not args.check:
        if args.project is not None:
            parser.error("--project only applies together with --check")
        mcp.run()
        return 0
    try:
        report = asyncio.run(_check(args.project))
    except JamaError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, ensure_ascii=False))
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
