#!/usr/bin/env python3
"""Keys, one file per site (ADR 0008). Standard library only; Python 3.9+.

Each key lives in ~/.buddy-council/keys/<site>.json, mode 600 in a 700 folder, and names the provider and the URL
it was saved for:

  {"provider": "testrail", "url": "https://acme.testrail.io", "username": "qa@acme.com", "api_key": "…"}
  {"provider": "jama", "url": "https://acme.jamacloud.com", "client_id": "…", "client_secret": "…"}
  {"provider": "jira", "url": "https://acme.atlassian.net", "username": "qa@acme.com", "api_token": "…"}
  {"provider": "jira", "url": "https://jira.acme.com", "personal_token": "…"}

Jama takes username + password instead on instances without API credentials, and a Jira key may add
"ssl_verify": false for a self-signed Server/Data Center certificate.

A site is a URL's host, its port when that isn't the scheme's default, and any path prefix: self-hosted Jama and
Jira can sit under one (jira.acme.com/jira). Every connector call can name its site, as a base URL or a host, and
the connector talks to the URL saved in that site's key file, so a key only ever reaches the site it was saved
for. A call that names no site uses this computer's only key for that provider. One key per site.

The connectors under mcp-servers/ import this file by path and look a site's key up on every call, so a key works
the moment it is saved. /bc:setup uses the command line, which never prints a key:

  path    --url URL --provider P     where the key for URL's site belongs, and whether one is saved there
  sites   [--provider P]             the saved sites
  check   --url URL --provider P     the saved key is complete and private (tightens its permissions)
  migrate [--force]                  copy the keys older setups saved into per-site files, once per computer
  forget  --url URL                  delete a site's key
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import re
import stat
import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any, Iterable

PROVIDERS = ("testrail", "jama", "jira")
NAMES = {"testrail": "TestRail", "jama": "Jama", "jira": "Jira"}
# The ways each provider signs in, in the order the connectors prefer them. A key holds exactly one.
AUTH = {
    "testrail": (("username", "api_key"),),
    "jama": (("client_id", "client_secret"), ("username", "password")),
    "jira": (("username", "api_token"), ("personal_token",)),
}
AUTH_WORDS = {
    ("username", "api_key"): "username + API key",
    ("client_id", "client_secret"): "OAuth API credentials",
    ("username", "password"): "username + password",
    ("username", "api_token"): "email + API token",
    ("personal_token",): "personal access token",
}
OPTIONS = {"jira": {"ssl_verify": bool}}
HTTPS_ONLY = {"jama"}  # the Jama connector has always refused to send its credentials over plain HTTP
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
# Where a pasted link stops being the site's address and becomes a page in the app.
APP_PATHS = {
    "testrail": re.compile(r"/index\.php(?:/|$)", re.I),
    "jama": re.compile(r"/(?:perspective\.req|rest)(?:/|$)", re.I),
    "jira": re.compile(r"/(?:browse|secure|rest|projects|issues|plugins|servicedesk|jira/(?:software|core|servicedesk))"
                       r"(?:/|$)", re.I),
}
MARKER = ".migrated"
CLOUD_JIRA = (".atlassian.net", ".jira.com")
# The server folder each old connector entry ran; an entry running anything else was never bc's.
SERVER_FOLDERS = {"testrail": "testrail-server", "jama": "jama-server"}


class KeyStoreError(Exception):
    """A problem with a saved key, worded for the person who has to fix it. It never carries a key."""


# ---------------------------------------------------------------------------
# Sites
# ---------------------------------------------------------------------------


def bc_home() -> Path:
    return Path(os.environ.get("BC_HOME") or Path.home() / ".buddy-council")


def keys_dir() -> Path:
    return bc_home() / "keys"


def _provider(provider: str) -> str:
    if provider not in PROVIDERS:
        raise KeyStoreError(f"Unknown provider {provider!r}; expected one of {', '.join(PROVIDERS)}.")
    return provider


def _split(url: Any) -> urllib.parse.SplitResult:
    text = str(url or "").strip()
    if not text:
        raise KeyStoreError("No site was given.")
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:  # an unclosed [ in the host, say
        raise KeyStoreError(f"{url!r} is not a site address.") from None
    if not parts.hostname:
        raise KeyStoreError(f"{url!r} is not a site address.")
    try:
        parts.port
    except ValueError:
        raise KeyStoreError(f"{url!r} has an invalid port.") from None
    return parts


def _authority(parts: urllib.parse.SplitResult) -> str:
    host = (parts.hostname or "").lower()
    authority = f"[{host}]" if ":" in host else host
    default = {"https": 443, "http": 80}.get(parts.scheme.lower())
    if parts.port and parts.port != default:
        authority += f":{parts.port}"
    return authority


def site_id(url: Any) -> str:
    """The site a URL or host belongs to: lowercase host, a port that isn't the default, and the path prefix."""
    parts = _split(url)
    return _authority(parts) + re.sub(r"/{2,}", "/", parts.path).rstrip("/")


def base_url(provider: str, url: Any) -> str:
    """The address a key is saved with: scheme, host, port, and path prefix, with any page of the app cut off."""
    _provider(provider)
    parts = _split(url)
    scheme = parts.scheme.lower()
    if scheme not in ("https", "http"):
        raise KeyStoreError(f"{url!r} is not an https:// address.")
    if parts.username or parts.password:
        raise KeyStoreError("A site address must not carry a name or password; those belong in the key.")
    if scheme == "http" and provider in HTTPS_ONLY and (parts.hostname or "").lower() not in LOOPBACK:
        raise KeyStoreError(f"Refusing to send {NAMES[provider]} credentials over plain HTTP ({url}). "
                            "Use the https:// address.")
    path = re.sub(r"/{2,}", "/", parts.path)
    cut = APP_PATHS[provider].search(path)
    if cut:
        path = path[: cut.start()]
    return f"{scheme}://{_authority(parts)}{path.rstrip('/')}"


def key_path(site: Any) -> Path:
    """Where the key for a site belongs. Pass a site id, a host, or a base URL."""
    return keys_dir() / (urllib.parse.quote(site_id(site), safe="") + ".json")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _load(path: Path) -> dict:
    """One key file, checked for shape and made private. Errors name the file, never its contents."""
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise KeyStoreError(f"Cannot read {path.name}: {exc.strerror}.") from None
    if not stat.S_ISREG(info.st_mode):
        raise KeyStoreError(f"{path.name} is not a regular file, so it is ignored.")
    if info.st_mode & 0o077:
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise KeyStoreError(f"{path.name} isn't valid JSON; save the key again with /bc:setup.") from None
    if not isinstance(data, dict) or data.get("provider") not in PROVIDERS:
        raise KeyStoreError(f"{path.name} names no known provider ({', '.join(PROVIDERS)}), so it is ignored.")
    try:
        data["url"] = base_url(data["provider"], data.get("url"))
    except KeyStoreError as exc:
        raise KeyStoreError(f"{path.name}: {exc}") from None
    return data


def _auth(provider: str, data: dict, strict: bool = False) -> tuple:
    """The sign-in style a key holds. With strict (saving), a key holding two styles is refused."""
    complete = [style for style in AUTH[provider]
                if all(isinstance(data.get(field), str) and data[field].strip() for field in style)]
    if complete and (not strict or len(complete) == 1):
        return complete[0]
    styles = " or ".join(" + ".join(style) for style in AUTH[provider])
    where = site_id(data["url"]) if data.get("url") else "this site"
    if complete:
        raise KeyStoreError(f"The {NAMES[provider]} key for {where} holds two ways to sign in; save one: {styles}.")
    raise KeyStoreError(f"The {NAMES[provider]} key for {where} is incomplete: it needs {styles}. "
                        "Run /bc:setup to save it again.")


def _scan() -> tuple[list[dict], list[str]]:
    """Every saved key's provider, site, and URL, plus a line for each file that was ignored."""
    folder = keys_dir()
    entries: list[dict] = []
    problems: list[str] = []
    if not folder.is_dir():
        return entries, problems
    for path in sorted(folder.glob("*.json")):
        if path.name.startswith("."):
            continue
        try:
            data = _load(path)
        except KeyStoreError as exc:
            problems.append(str(exc))
            continue
        site = site_id(data["url"])
        if path.name != key_path(site).name:
            problems.append(f"{path.name} holds the key for {site}, which belongs in {key_path(site).name}, "
                            "so it is ignored.")
            continue
        entries.append({"provider": data["provider"], "site": site, "url": data["url"], "path": str(path)})
    return entries, problems


def sites(provider: str | None = None) -> list[dict]:
    """The saved sites — provider, site, url, and file — without any key."""
    if provider is not None:
        _provider(provider)
    return [entry for entry in _scan()[0] if provider is None or entry["provider"] == provider]


def _matches(want: str, saved: list[dict]) -> list[dict]:
    exact = [entry for entry in saved if want == entry["site"] or want.startswith(entry["site"] + "/")]
    if exact:
        longest = max(len(entry["site"]) for entry in exact)
        return [entry for entry in exact if len(entry["site"]) == longest]
    if "/" in want:
        return []
    return [entry for entry in saved if entry["site"].split("/", 1)[0] == want]


def _choose(provider: str, url: Any, saved: list[dict]) -> dict:
    name = NAMES[provider]
    listed = ", ".join(entry["site"] for entry in saved)
    if not str(url or "").strip():
        if len(saved) == 1:
            return saved[0]
        if not saved:
            raise KeyStoreError(f"No {name} key is saved on this computer. Run /bc:setup to add one.")
        raise KeyStoreError(f"This computer has {name} keys for {len(saved)} sites ({listed}); "
                            "pass `site` to say which one.")
    want = site_id(url)
    found = _matches(want, saved)
    if len(found) == 1:
        return found[0]
    if found:
        raise KeyStoreError(f"{want} has {name} keys for {', '.join(entry['site'] for entry in found)}; "
                            "pass the full address as `site`.")
    known = f" Saved {name} sites: {listed}." if saved else ""
    raise KeyStoreError(f"No {name} key is saved for {want}.{known} Run /bc:setup with a link to {want} to add it.")


def read_key(url: Any, provider: str) -> dict:
    """The key for the site a URL or host names, or the provider's only key when `url` is empty.

    Returns the credential fields plus `provider`, `url` (the saved address — the only one the key may be sent
    to), `site`, and `auth` (the sign-in style, in words). The first lookup for a provider with no saved key
    copies the keys older setups saved (`migrate_legacy`), so connectors keep working on the day they update.
    """
    _provider(provider)
    saved = sites(provider)
    if not saved:
        with contextlib.suppress(Exception):
            migrate_legacy()
        saved = sites(provider)
    chosen = _choose(provider, url, saved)
    data = _load(Path(chosen["path"]))
    style = _auth(provider, data)
    key: dict[str, Any] = {field: data[field].strip() for field in style}
    for option, kind in OPTIONS.get(provider, {}).items():
        if isinstance(data.get(option), kind):
            key[option] = data[option]
    key.update(provider=provider, url=data["url"], site=chosen["site"], auth=AUTH_WORDS[style])
    return key


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _private_dir(folder: Path) -> None:
    home = bc_home()
    if not home.exists():
        home.mkdir(parents=True, mode=0o700)
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)


def _write(path: Path, data: dict) -> None:
    _private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def write_key(provider: str, url: Any, fields: dict) -> Path:
    """Save the key for a site, replacing the one saved there before. Returns the file's path."""
    base = base_url(provider, url)
    site = site_id(base)
    values = {name: (value.strip() if isinstance(value, str) else value) for name, value in (fields or {}).items()}
    allowed = {field for style in AUTH[provider] for field in style} | set(OPTIONS.get(provider, {}))
    unknown = sorted(set(values) - allowed - {"provider", "url"})
    if unknown:
        raise KeyStoreError(f"A {NAMES[provider]} key takes {', '.join(sorted(allowed))}, not {', '.join(unknown)}.")
    style = _auth(provider, {**values, "url": base}, strict=True)
    data: dict[str, Any] = {"provider": provider, "url": base, **{field: values[field] for field in style}}
    for option, kind in OPTIONS.get(provider, {}).items():
        if values.get(option) is not None:
            if not isinstance(values[option], kind):
                raise KeyStoreError(f"{option} must be {kind.__name__}.")
            data[option] = values[option]
    path = key_path(site)
    if path.exists():
        try:
            other = _load(path)["provider"]
        except KeyStoreError:
            other = provider  # an unreadable file for this site is replaced
        if other != provider:
            raise KeyStoreError(f"{site} already has a {NAMES[other]} key, and a site keeps one key.")
    _write(path, data)
    return path


def forget(url: Any) -> list[dict]:
    """Delete the key a URL or host names. Returns what was deleted."""
    found = _matches(site_id(url), sites())
    if len(found) > 1:
        raise KeyStoreError(f"{site_id(url)} matches {len(found)} saved sites; pass the full address.")
    for entry in found:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(entry["path"])
    return [{key: entry[key] for key in ("provider", "site")} for entry in found]


def check(url: Any, provider: str) -> dict:
    """Whether the key for a site is saved, complete, and private. Tightens its permissions; never shows it."""
    base = base_url(provider, url)
    site = site_id(base)
    path = key_path(site)
    result: dict[str, Any] = {"ok": False, "provider": provider, "site": site, "path": str(path)}
    if not path.exists():
        return {**result, "problem": f"no {NAMES[provider]} key is saved for {site}"}
    try:
        data = _load(path)
        if data["provider"] != provider:
            raise KeyStoreError(f"{path.name} holds a {NAMES[data['provider']]} key, not a {NAMES[provider]} one.")
        if site_id(data["url"]) != site:
            raise KeyStoreError(f"{path.name} names {site_id(data['url'])}, not {site}.")
        style = _auth(provider, data, strict=True)
        _private_dir(path.parent)
    except KeyStoreError as exc:
        return {**result, "problem": str(exc)}
    return {**result, "ok": True, "url": data["url"], "auth": AUTH_WORDS[style]}


# ---------------------------------------------------------------------------
# Migration from the one-site-per-computer files
# ---------------------------------------------------------------------------


def _tilde(path: Path, home: Path) -> str:
    with contextlib.suppress(ValueError):
        return "~/" + path.relative_to(home).as_posix()
    return str(path)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _servers(data: Any) -> dict:
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return servers if isinstance(servers, dict) else {}


def _bc_root(provider: str, entry: Any) -> str | None:
    """The plugin folder an old connector entry ran bc's server from, or None when the entry isn't bc's.

    Older setups ran each server with `--directory <plugin folder>/mcp-servers/<provider>-server`. The folder comes
    back as written — absolute, or a placeholder such as ${CLAUDE_PLUGIN_ROOT}.
    """
    if provider not in SERVER_FOLDERS or not isinstance(entry, dict):
        return None
    args = entry.get("args") if isinstance(entry.get("args"), list) else []
    pattern = re.compile(rf"(?:^|/)mcp-servers/{SERVER_FOLDERS[provider]}(?:/server\.py)?/?$")
    for word in (entry.get("command"), entry.get("cwd"), *args):
        match = pattern.search(word.strip()) if isinstance(word, str) else None
        if match:
            return word.strip()[: match.start()]
    return None


def _is_bc_plugin(folder: Path) -> bool:
    for manifest in (folder / "plugin.json", folder / ".claude-plugin" / "plugin.json"):
        data = _read_json(manifest)
        if isinstance(data, dict) and data.get("name") == "bc":
            return True
    return False


def _connector_settings(home: Path) -> list[tuple[str, dict]]:
    """bc's own old connector entries, from every file older setups wrote them to.

    Older setups registered a `testrail` and a `jama` entry, each running this plugin's server folder, in
    ~/.copilot/mcp-config.json and in the plugin folder's .mcp.json. Only those entries count — never another
    plugin's settings, nor a server that isn't bc's — and every bc plugin folder counts, not only the newest, so
    folders that disagree leave the site unknown. A bc plugin folder is one whose manifest names `bc`: a Claude Code
    version folder, a marketplace copy, a Copilot install, or the folder bc's Copilot entries ran from.
    """
    copilot = home / ".copilot" / "mcp-config.json"
    folders: list[Path] = []
    for pattern in (".claude/plugins/cache/*/*/*", ".claude/plugins/marketplaces/*", ".copilot/installed-plugins/*/*"):
        folders.extend(sorted(home.glob(pattern)))
    for provider, entry in _servers(_read_json(copilot)).items():
        root = _bc_root(provider, entry)
        if root and os.path.isabs(root):
            folders.append(Path(root))
    files, seen = [copilot], set()
    for folder in folders:
        try:
            real = folder.resolve()
        except (OSError, RuntimeError):
            continue
        if real not in seen and _is_bc_plugin(folder):
            seen.add(real)
            files.append(folder / ".mcp.json")
    found: list[tuple[str, dict]] = []
    for path in files:
        entries = {provider: entry for provider, entry in _servers(_read_json(path)).items()
                   if _bc_root(provider, entry) is not None}
        if entries:
            found.append((_tilde(path, home), entries))
    return found


def _expand(text: str, home: Path) -> Path:
    if text == "~" or text.startswith("~/"):
        return home / text[2:]
    return Path(os.path.expandvars(text))


def _old_secrets_file(home: Path, provider: str, named: str | None) -> Path | None:
    """The file an old connector read its key from: the first readable one of BC_SECRETS_FILE, the usual file, and
    (TestRail) the flat file before it."""
    chain = [_expand(named, home)] if named else []
    chain.append(home / ".buddy-council" / "secrets.json")
    if provider == "testrail":
        chain.append(home / ".buddy-council-secrets.json")
    return next((path for path in chain if _read_json(path) is not None), None)


def _legacy_section(home: Path, provider: str, settings: list[tuple[str, dict]]) -> dict | None:
    """The provider's section of the secrets file its old connectors read, or None.

    Raises when bc's old entries read different keys from different files: which one was in use is then unknown.
    """
    named: list[str | None] = []
    for _, entries in settings:
        if provider in entries:
            env = entries[provider].get("env")
            value = env.get("BC_SECRETS_FILE") if isinstance(env, dict) else None
            named.append(value.strip() if isinstance(value, str) and value.strip() else None)
    files: list[Path] = []
    for name in named or [None]:
        path = _old_secrets_file(home, provider, name)
        if path is not None and path not in files:
            files.append(path)
    sections = []
    for path in files:
        data = _read_json(path)
        section = data.get(provider) if isinstance(data, dict) else None
        if isinstance(section, dict) and section:
            sections.append((path, section))
    if len({json.dumps(section, sort_keys=True) for _, section in sections}) > 1:
        where = " and ".join(_tilde(path, home) for path, _ in sections)
        raise KeyStoreError(f"the old {NAMES[provider]} key differs between {where}, so which one was in use is unknown")
    return sections[0][1] if sections else None


def _read_env(path: Path) -> dict:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        name, _, value = text.partition("=")
        name = name.strip()
        if name.startswith("export "):
            name = name[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    return values


def _jira_fields(env: dict) -> dict:
    """The auth the old Jira connector used: mcp-atlassian prefers email + API token on Cloud, a PAT elsewhere."""
    url = env.get("JIRA_URL", "")
    cloud = False
    with contextlib.suppress(KeyStoreError):
        cloud = _split(url).hostname.lower().endswith(CLOUD_JIRA)
    pair = {"username": env.get("JIRA_USERNAME", ""), "api_token": env.get("JIRA_API_TOKEN", "")}
    pat = {"personal_token": env.get("JIRA_PERSONAL_TOKEN", "")}
    has_pair = all(value.strip() for value in pair.values())
    fields = pair if (cloud and has_pair) or (has_pair and not pat["personal_token"].strip()) else pat
    if env.get("JIRA_SSL_VERIFY", "").strip().lower() in ("false", "0", "no"):
        fields = {**fields, "ssl_verify": False}
    return fields


def _migrate_one(report: dict, provider: str, recorded: Iterable[tuple[str, str]], fields: dict) -> None:
    name = NAMES[provider]
    found: dict[str, tuple[str, str]] = {}
    invalid: list[str] = []
    for label, url in recorded:
        try:
            found.setdefault(site_id(base_url(provider, url)), (label, url))
        except KeyStoreError:
            invalid.append(label)
    if not found:
        reason = (f"the site saved beside the old {name} key ({', '.join(invalid)}) is not a valid address" if invalid
                  else f"no site was saved beside the old {name} key")
        report["skipped"].append({"provider": provider, "reason": reason})
        return
    if len(found) > 1:
        report["skipped"].append({"provider": provider, "reason": f"the old {name} key was set up for "
                                  f"{len(found)} sites ({', '.join(sorted(found))}), so its site is unknown"})
        return
    site, (label, url) = next(iter(found.items()))
    if key_path(site).exists():
        report["kept"].append({"provider": provider, "site": site})
        return
    try:
        write_key(provider, url, fields)
    except KeyStoreError as exc:
        report["skipped"].append({"provider": provider, "site": site, "reason": str(exc)})
        return
    report["migrated"].append({"provider": provider, "site": site, "from": label})


def migrate_legacy(legacy_home: Any = None, force: bool = False) -> dict:
    """Copy the keys older setups saved into per-site files, once per computer.

    Older setups kept one TestRail and one Jama key in ~/.buddy-council/secrets.json and the Jira token in
    ~/.buddy-council/atlassian.env. Each key's site comes from what those setups recorded beside it: the secrets'
    own base_url, the connector settings they registered (TESTRAIL_BASE_URL, JAMA_BASE_URL — bc's own entries
    only, from every file they were written to), or JIRA_URL. A key with no recorded site, or two different ones,
    stays where it is: a site is never guessed. A saved per-site key is never replaced, one provider's trouble never
    holds up another's, the old files are left for /bc:setup to remove, and the report names sites only.
    `legacy_home` reads the old files from another home folder; the keys are always written to keys_dir().
    """
    home = Path(legacy_home).expanduser() if legacy_home else Path.home()
    report: dict[str, Any] = {"migrated": [], "kept": [], "skipped": [], "already": False}
    marker = keys_dir() / MARKER
    if marker.exists() and not force:
        report["already"] = True
        return report
    settings = _connector_settings(home)
    for provider, setting in (("testrail", "TESTRAIL_BASE_URL"), ("jama", "JAMA_BASE_URL")):
        try:
            section = _legacy_section(home, provider, settings)
            if not section:
                continue
            recorded = [("secrets.json", section["base_url"])] if isinstance(section.get("base_url"), str) else []
            for label, entries in settings:
                entry = entries.get(provider)
                env = entry.get("env") if isinstance(entry, dict) else None
                if isinstance(env, dict) and isinstance(env.get(setting), str) and env[setting].strip():
                    recorded.append((label, env[setting]))
            try:
                style = _auth(provider, section)
            except KeyStoreError:
                style = next(iter(AUTH[provider]))
            _migrate_one(report, provider, recorded, {field: section.get(field) for field in style})
        except (KeyStoreError, OSError) as exc:  # one provider's trouble never holds up the others
            report["skipped"].append({"provider": provider, "reason": str(exc)})
    try:
        env = _read_env(home / ".buddy-council" / "atlassian.env")
        if env:
            url = env.get("JIRA_URL", "").strip()
            _migrate_one(report, "jira", [("atlassian.env", url)] if url else [], _jira_fields(env))
    except (KeyStoreError, OSError) as exc:
        report["skipped"].append({"provider": "jira", "reason": str(exc)})
    _private_dir(marker.parent)
    stamp = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _write(marker, {"migrated_at": stamp, **{k: report[k] for k in ("migrated", "kept", "skipped")}})
    return report


# ---------------------------------------------------------------------------
# Command line — prints JSON, never a key
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Buddy-Council keys, one file per site. Never prints a key.")
    sub = parser.add_subparsers(dest="action", required=True)
    path = sub.add_parser("path", help="where the key for a site belongs")
    path.add_argument("--url", required=True)
    path.add_argument("--provider", required=True, choices=PROVIDERS)
    listing = sub.add_parser("sites", help="the saved sites")
    listing.add_argument("--provider", choices=PROVIDERS)
    checking = sub.add_parser("check", help="the saved key is complete and private")
    checking.add_argument("--url", required=True)
    checking.add_argument("--provider", required=True, choices=PROVIDERS)
    migrating = sub.add_parser("migrate", help="copy the keys older setups saved into per-site files")
    migrating.add_argument("--force", action="store_true", help="run again even though it ran before")
    forgetting = sub.add_parser("forget", help="delete a site's key")
    forgetting.add_argument("--url", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "path":
            base = base_url(args.provider, args.url)
            target = key_path(base)
            _private_dir(target.parent)
            saved = None
            with contextlib.suppress(KeyStoreError):
                saved = _load(target)["provider"] if target.exists() else None
            result: dict[str, Any] = {"ok": True, "site": site_id(base), "url": base, "path": str(target),
                                      "saved": saved is not None, "saved_provider": saved}
        elif args.action == "sites":
            entries, problems = _scan()
            result = {"ok": True, "problems": problems,
                      "sites": [{k: e[k] for k in ("provider", "site", "url")} for e in entries
                                if args.provider is None or e["provider"] == args.provider]}
        elif args.action == "check":
            result = check(args.url, args.provider)
        elif args.action == "migrate":
            result = {"ok": True, **migrate_legacy(force=args.force)}
        else:
            result = {"ok": True, "forgot": forget(args.url)}
    except (KeyStoreError, OSError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
