#!/usr/bin/env python3
"""The tools /bc:automate drives the app with, installed by /bc:setup. Standard library only; Python 3.9+.

bc keeps its own pinned copies, the way uv provides Python: never on PATH, never shared with anything else on the Mac,
and never set in a shell profile, JAVA_HOME, or ANDROID_HOME. Everything lives under ~/.buddy-council:

  tools/node/v26.13.0-darwin-arm64/   Node.js, by version and SHA-256 (pins.json)
  tools/appium-mcp/1.99.2-<lock>/     appium-mcp, as appium-mcp/package-lock.json says, via npm ci --ignore-scripts
  tools/jdk/, tools/android-sdk/      only for Android on appium-mcp's built-in drivers, on a Mac without them
  tools/appium-mcp.json               how the plugin's appium connector starts appium-mcp, written once it answered
  automation/                         what the server writes (page sources, screenshots) and the logs

Subcommands, each printing one JSON object to stdout (progress goes to stderr):

  detect  --project DIR                     read the test project and this Mac: stack, setup script, Appium,
                                            phone, app, language, and what ensure would install. Runs nothing
                                            from the project
  status  --project DIR                     installed against pinned, wiring, and what is pending
  ensure  --project DIR [--shared jdk,android-sdk]
                                            install or upgrade the pinned tools, prove the server answers, record
                                            how the plugin's appium connector starts it, remove pins nothing uses,
                                            and record the result in <project>/.buddy-council/sources.json
  smoke   --project DIR                     read one page source from the phone; installs nothing. Run it after
                                            ensure when ensure reports "phone_check": "due"
  start   --project DIR                     start the project's own Appium server on this Mac (127.0.0.1 only)
  stop                                      stop the Appium server that start started
  prune                                     remove pins and leftovers nothing points at any more
  project --project DIR --mode check|fix    run the project's own setup script and report it. Runs the project's
                                            code, so the bash hook never approves it by itself
  remove  --project DIR                     delete bc's tools; the appium connector goes quiet. Never approved by itself
  serve                                     the plugin's appium connector (scripts/bc-mcp runs it): appium-mcp as
                                            ensure recorded it, or an MCP server with no tools until then

Downloads use this Mac's own settings: the environment's and macOS's proxy, npm's proxy and certificate settings
(~/.npmrc), the keychain's certificates when the bundled ones fail, and NODEJS_ORG_MIRROR for Node. Every file is
checked against its pinned SHA-256, and every npm package against the lockfile's integrity hash, so a mirror can
never change what gets installed.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import platform as platform_module
import queue
import re
import shutil
import signal
import socket
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bc_setup  # noqa: E402 — the only code that saves a project's settings

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent
LOCAL_ONLY_URLS = r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:[0-9]{1,5})?(/[^?#]*)?$"
# Auto-approved by hooks/auto-approve-mcp.sh: appium-mcp's read-only tools, plus page sources and screenshots,
# which it writes into ~/.buddy-council/automation. Keep the two lists identical.
READ_TOOLS = (
    "appium_mobile_device_info",
    "appium_find_element",
    "appium_find_elements",
    "appium_get_text",
    "appium_get_element_attribute",
    "appium_get_active_element",
    "appium_get_window_size",
    "generate_locators",
    "appium_get_page_source",
    "appium_screenshot",
)
NEEDED_TOOLS = ("appium_session_management", "appium_get_page_source")
SETUP_SCRIPTS = ("setup", "setup.sh", "scripts/setup.sh", "bin/setup")
SKIP_DIRS = {
    "node_modules", "build", "dist", "out", "target", "bin", "obj", "logs", "Pods", "DerivedData", "venv",
    "__pycache__", "test-output", "test_output", "allure-results", "allure-report", "reports", "screenshots",
}
SCAN_LIMIT = 4000  # files read while looking for a test project
HEAD_BYTES = 16_384
# Every subcommand must finish within 10 minutes, the longest a CLI's shell tool waits (Claude Code's limit):
HANDSHAKE_SECONDS = 60
SESSION_SECONDS = 330  # the first session on an iPhone builds WebDriverAgent
SERVER_START_SECONDS = 60
NPM_SECONDS = 480  # a slower install is retried next run, with npm's cache warm
PROJECT_SECONDS = 540
DOWNLOAD_TIMEOUT = 30
KEEP_LOGS = 30
KEEP_FILES_DAYS = 7
PHONE_DRIVERS = {"ios": "xcuitest", "android": "uiautomator2"}


class ToolError(Exception):
    """A failure to report to the user as one plain sentence."""


class DownloadError(ToolError):
    """A download that failed; `missing` when the server says the file isn't there (so another file may be)."""

    def __init__(self, message: str, missing: bool = False):
        super().__init__(message)
        self.missing = missing


# ---------------------------------------------------------------------------
# Paths and small helpers
# ---------------------------------------------------------------------------


def bc_home() -> Path:
    return Path(os.environ.get("BC_HOME") or Path.home() / ".buddy-council")


def tools_dir() -> Path:
    return bc_home() / "tools"


def work_dir() -> Path:
    return bc_home() / "automation"


def logs_dir() -> Path:
    folder = work_dir() / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def server_record() -> Path:
    return tools_dir() / "appium-mcp.json"


def pins_file() -> Path:
    return Path(os.environ.get("BC_PINS_FILE") or SCRIPT_DIR / "automation" / "pins.json")


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def say(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def tilde(path: Any) -> str:
    text = str(path)
    home = str(Path.home())
    return "~" + text[len(home):] if text == home or text.startswith(home + os.sep) else text


def plugin_version() -> str:
    for manifest in (PLUGIN_ROOT / "plugin.json", PLUGIN_ROOT / ".claude-plugin" / "plugin.json"):
        with contextlib.suppress(OSError, ValueError):
            return str(json.loads(manifest.read_text(encoding="utf-8")).get("version") or "dev")
    return "dev"


def read_json(path: Path, default: Any = None) -> Any:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return default
    if not text.strip():
        return default
    return json.loads(text)


def write_json(path: Path, data: Any, mode: int | None = None) -> None:
    """Replace the file atomically, keeping its permissions; a symlinked file is written where it points."""
    path = Path(path)
    if path.is_symlink():
        path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except FileNotFoundError:
            mode = 0o600
    handle, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(data, out, indent=2, ensure_ascii=False)
            out.write("\n")
        os.chmod(temp, mode)
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise


def run_text(command: list[str], timeout: int = 30, cwd: Path | None = None, env: dict | None = None) -> tuple[int, str]:
    """Run a command that needs no input; (exit code, stdout + stderr). A missing program is exit code 127."""
    try:
        done = subprocess.run(
            command, cwd=str(cwd) if cwd else None, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=timeout,
        )
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError as exc:
        return 126, str(exc)
    return done.returncode, done.stdout.decode("utf-8", errors="replace")


def tail(path: Path, lines: int = 6) -> str:
    with contextlib.suppress(OSError):
        rows = [row.strip() for row in Path(path).read_text(encoding="utf-8", errors="replace").splitlines() if row.strip()]
        return " | ".join(rows[-lines:])
    return ""


def one_line(text: str, limit: int = 300) -> str:
    rows = [row.strip() for row in str(text).splitlines() if row.strip()]
    first = next((row for row in rows if not row.startswith("at ")), rows[0] if rows else "")
    return first if len(first) <= limit else first[: limit - 1] + "…"


def remove_tree(path: Path) -> None:
    """Delete a file or folder, but only inside ~/.buddy-council."""
    path = Path(path)
    home = bc_home().resolve()
    where = path.parent.resolve() / path.name
    if home != where and home not in where.parents:
        raise ToolError(f"refusing to delete {path}: it is outside {home}")
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


@contextlib.contextmanager
def tools_lock():
    """One install at a time on this Mac, across projects and CLIs."""
    tools_dir().mkdir(parents=True, exist_ok=True)
    with open(tools_dir() / ".lock", "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            say("Another bc setup is installing tools on this Mac; waiting for it to finish…")
            fcntl.flock(handle, fcntl.LOCK_EX)
        yield


# ---------------------------------------------------------------------------
# Pins
# ---------------------------------------------------------------------------


def load_pins() -> dict:
    pins = read_json(pins_file())
    if not isinstance(pins, dict) or not isinstance(pins.get("node"), dict) or not isinstance(pins.get("appium_mcp"), dict):
        raise ToolError(f"{pins_file()} is not a valid pins file")
    return pins


def platform_key() -> str | None:
    """darwin-arm64 or darwin-x64; None off macOS, where these tools aren't set up yet."""
    if sys.platform != "darwin":
        return None
    if platform_module.machine().lower() in ("arm64", "aarch64"):
        return "darwin-arm64"
    code, out = run_text(["/usr/sbin/sysctl", "-n", "hw.optional.arm64"], timeout=10)
    return "darwin-arm64" if code == 0 and out.strip() == "1" else "darwin-x64"


def lockfile_dir(pins: dict) -> Path:
    return pins_file().parent / str(pins["appium_mcp"].get("lockfile_dir") or "appium-mcp")


def appium_pin(pins: dict) -> str:
    digest = hashlib.sha256()
    for name in ("package.json", "package-lock.json"):
        digest.update((lockfile_dir(pins) / name).read_bytes())
    return f"{pins['appium_mcp']['version']}-{digest.hexdigest()[:12]}"


def node_home(pins: dict, key: str) -> Path:
    return tools_dir() / "node" / f"v{pins['node']['version']}-{key}"


def node_exe(home: Path) -> Path:
    return home / "bin" / "node"


def npm_cli(home: Path) -> Path:
    return home / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"


def appium_home(pins: dict) -> Path:
    return tools_dir() / "appium-mcp" / appium_pin(pins)


def appium_entry(pins: dict, home: Path) -> Path:
    return home / str(pins["appium_mcp"].get("entry") or "node_modules/appium-mcp/dist/index.js")


def jdk_home(pins: dict) -> Path:
    return tools_dir() / "jdk" / str(pins["jdk"]["version"])


def android_home() -> Path:
    return tools_dir() / "android-sdk"


# ---------------------------------------------------------------------------
# Downloads: the Mac's proxy, npm settings, and certificates; every file checked against its pin
# ---------------------------------------------------------------------------


def npm_settings() -> dict:
    """The proxy and certificate settings npm uses, from ~/.npmrc and npm_config_* variables."""
    values: dict[str, str] = {}
    rc = Path(os.environ.get("NPM_CONFIG_USERCONFIG") or os.environ.get("npm_config_userconfig") or Path.home() / ".npmrc")
    with contextlib.suppress(OSError):
        for line in rc.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line[0] in "#;" or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = re.sub(r"\$\{([A-Za-z0-9_]+)\}", lambda m: os.environ.get(m.group(1), ""), value.strip().strip('"'))
            values[key.strip().lower()] = value
    for key in ("https-proxy", "proxy", "noproxy", "cafile", "strict-ssl"):
        name = "npm_config_" + key.replace("-", "_")
        found = os.environ.get(name) or os.environ.get(name.upper())
        if found:
            values[key] = found
    return values


def proxies() -> dict:
    """The environment's proxy, else macOS's network settings, else npm's."""
    found = dict(urllib.request.getproxies())
    if "https" not in found:
        npm = npm_settings()
        proxy = npm.get("https-proxy") or npm.get("proxy")
        if proxy:
            found["https"] = proxy
            found.setdefault("http", npm.get("proxy") or proxy)
    return found


def mac_certificates() -> list[str]:
    """PEM certificates from the macOS keychains, npm's cafile, and NODE_EXTRA_CA_CERTS — a company's own root CA."""
    texts = []
    for chain in ("/System/Library/Keychains/SystemRootCertificates.keychain", "/Library/Keychains/System.keychain",
                  str(Path.home() / "Library" / "Keychains" / "login.keychain-db")):
        if os.path.exists(chain):
            code, out = run_text(["/usr/bin/security", "find-certificate", "-a", "-p", chain], timeout=60)
            if code == 0:
                texts.append(out)
    for extra in (os.environ.get("NODE_EXTRA_CA_CERTS"), npm_settings().get("cafile"), os.environ.get("SSL_CERT_FILE")):
        if extra and os.path.isfile(extra):
            with contextlib.suppress(OSError):
                texts.append(Path(extra).read_text(encoding="utf-8", errors="replace"))
    return re.findall(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", "\n".join(texts), flags=re.S)


def ssl_context(kind: str) -> ssl.SSLContext:
    if kind == "unverified":  # only when npm itself is set to strict-ssl=false; the pinned SHA-256 still guards the file
        return ssl._create_unverified_context()  # noqa: S323
    context = ssl.create_default_context()
    if kind == "mac":
        for pem in mac_certificates():
            with contextlib.suppress(ssl.SSLError, ValueError):
                context.load_verify_locations(cadata=pem)
    return context


def is_certificate_error(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(exc)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url: str, target: Path, context: ssl.SSLContext, label: str) -> None:
    handlers: list[Any] = [urllib.request.ProxyHandler(proxies()), urllib.request.HTTPSHandler(context=context)]
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, headers={"User-Agent": f"buddy-council/{plugin_version()}"})
    with opener.open(request, timeout=DOWNLOAD_TIMEOUT) as response, open(target, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done, mark = 0, 0.25
        for block in iter(lambda: response.read(1 << 20), b""):
            out.write(block)
            done += len(block)
            if total and done / total >= mark and mark < 1:
                say(f"   {label}: {int(done * 100 / total)}%")
                mark += 0.25


def download(url: str, target: Path, sha256: str, label: str) -> Path:
    """Download url to target and check its SHA-256; a finished, matching file is reused.

    Retries a dropped connection, but moves on to the keychain's certificates only after a certificate error, and
    stops at once when the file isn't there — so an unreachable host costs about a minute and a half, not ten.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and file_sha256(target) == sha256:
        return target
    part = target.with_name(target.name + ".part")
    kinds = ["default", "mac"] + (["unverified"] if npm_settings().get("strict-ssl", "").lower() == "false" else [])
    failure: BaseException | None = None
    for kind in kinds:
        for attempt in range(3):
            try:
                fetch(url, part, ssl_context(kind), label)
                failure = None
                break
            except urllib.error.HTTPError as exc:
                failure = exc
                if 400 <= exc.code < 500:
                    break  # the server answered: asking again won't change it
            except (urllib.error.URLError, OSError, ssl.SSLError) as exc:
                failure = exc
                if is_certificate_error(exc) or isinstance(getattr(exc, "reason", None), FileNotFoundError):
                    break
            if attempt < 2:
                time.sleep(2 + attempt * 3)
        if failure is None or not is_certificate_error(failure):
            break  # done, or a failure another trust store can't fix
    if failure is not None:
        with contextlib.suppress(OSError):
            part.unlink()
        missing = (isinstance(failure, urllib.error.HTTPError) and failure.code in (404, 410)) or isinstance(
            getattr(failure, "reason", None), FileNotFoundError)
        raise DownloadError(f"couldn't download {url}: {getattr(failure, 'reason', None) or failure}", missing)
    actual = file_sha256(part)
    if actual != sha256:
        part.unlink()
        raise DownloadError(f"{url} isn't the pinned file (SHA-256 {actual[:12]}…, expected {sha256[:12]}…); nothing was installed")
    os.replace(part, target)
    return target


# ---------------------------------------------------------------------------
# Unpacking, without letting an archive write outside its folder
# ---------------------------------------------------------------------------


def check_member(name: str, archive: Path) -> None:
    if name.startswith("/") or ".." in Path(name).parts:
        raise ToolError(f"{archive.name} has an unsafe path in it ({name}); nothing was installed")


def walk(path: str, links: set, archive: Path, entry: str) -> str:
    """Resolve a path inside the archive by its words alone, refusing to pass through any link or leave the folder.

    Refusing every path that goes through a link is what makes the words alone enough: a link can then only
    point at a place inside the folder, reached without another link, so where it really leads is where it says.
    """
    parts: list[str] = []
    for word in path.split("/"):
        if word in ("", "."):
            continue
        if word == "..":
            if not parts:
                raise ToolError(f"{archive.name} points outside its folder ({entry}); nothing was installed")
            if "/".join(parts) in links:  # ".." after a link climbs from wherever the link points
                raise ToolError(f"{archive.name} has a path through a link ({entry}); nothing was installed")
            parts.pop()
            continue
        if "/".join(parts) in links:
            raise ToolError(f"{archive.name} has a path through a link ({entry}); nothing was installed")
        parts.append(word)
    return "/".join(parts)


def check_entries(entries: list[tuple[str, str | None, str | None]], archive: Path) -> None:
    """Refuse an archive that could write outside its folder. Each entry is (name, symlink target, hardlink target).

    Names may not be absolute, climb out, pass through an earlier link, or replace one. A symlink must point at a
    place inside the folder without passing through another link; a hardlink must name a plain entry.
    """
    links: set[str] = set()
    for name, symlink, hardlink in entries:
        check_member(name, archive)
        here = walk(name, links, archive, name)
        if here in links:
            raise ToolError(f"{archive.name} replaces a link ({name}); nothing was installed")
        if symlink is not None:
            if symlink.startswith("/"):
                raise ToolError(f"{archive.name} links outside its folder ({name}); nothing was installed")
            parent = here.rsplit("/", 1)[0] if "/" in here else ""
            walk(f"{parent}/{symlink}" if parent else symlink, links, archive, name)
            links.add(here)
        if hardlink is not None:
            check_member(hardlink, archive)
            if walk(hardlink, links, archive, name) in links:
                raise ToolError(f"{archive.name} hardlinks a link ({name}); nothing was installed")


def unpack_tar(archive: Path, folder: Path) -> None:
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        entries: list[tuple[str, str | None, str | None]] = []
        for member in members:
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ToolError(f"{archive.name} has an unexpected entry ({member.name}); nothing was installed")
            entries.append((member.name, member.linkname if member.issym() else None,
                            member.linkname if member.islnk() else None))
        check_entries(entries, archive)
        if hasattr(tarfile, "data_filter"):
            tar.extractall(folder, members=members, filter="tar")  # type: ignore[call-arg]
        else:
            tar.extractall(folder, members=members)  # noqa: S202 — check_entries refused anything that could escape


def unpack_zip(archive: Path, folder: Path) -> None:
    with zipfile.ZipFile(archive) as packed:
        entries: list[tuple[str, str | None, str | None]] = []
        for info in packed.infolist():
            is_link = stat.S_ISLNK(info.external_attr >> 16)
            target = packed.read(info).decode("utf-8", errors="replace") if is_link else None
            entries.append((info.filename, target, None))
        check_entries(entries, archive)
    folder.mkdir(parents=True, exist_ok=True)
    code, out = run_text(["/usr/bin/unzip", "-q", "-o", str(archive), "-d", str(folder)], timeout=900)
    if code != 0:
        raise ToolError(f"couldn't unpack {archive.name}: {one_line(out)}")


def only_child(folder: Path) -> Path:
    children = [child for child in folder.iterdir() if not child.name.startswith(".")]
    if len(children) != 1 or not children[0].is_dir():
        raise ToolError(f"expected one folder inside {folder.name}, found {len(children)}")
    return children[0]


def install_unpacked(archive: Path, destination: Path, unpack) -> None:
    """Unpack into a staging folder beside the destination, then move its one top folder into place."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=str(destination.parent)))
    try:
        unpack(archive, staging)
        inner = only_child(staging)
        if destination.exists() or destination.is_symlink():
            remove_tree(destination)
        os.rename(inner, destination)
    finally:
        remove_tree(staging)


# ---------------------------------------------------------------------------
# Installers
# ---------------------------------------------------------------------------


def node_works(home: Path, version: str) -> bool:
    code, out = run_text([str(node_exe(home)), "--version"], timeout=30)
    return code == 0 and out.strip() == f"v{version}"


def install_node(pins: dict, key: str) -> tuple[Path, bool]:
    """bc's own Node, by version and checksum. (home, whether it was installed now)."""
    spec = pins["node"]
    home = node_home(pins, key)
    if node_works(home, spec["version"]):
        return home, False
    choices = (spec.get("files") or {}).get(key) or []
    if not choices:
        raise ToolError(f"no Node {spec['version']} is pinned for {key}")
    base = (os.environ.get(spec.get("mirror_env") or "NODEJS_ORG_MIRROR") or spec["base_url"]).rstrip("/")
    problems = []
    for choice in choices:
        if choice["name"].endswith(".xz") and not lzma_available():
            continue
        url = f"{base}/v{spec['version']}/{choice['name']}"
        say(f"Downloading Node {spec['version']} ({choice.get('mb', '?')} MB) from {urllib.parse.urlparse(url).netloc or url}…")
        try:
            archive = download(url, tools_dir() / ".downloads" / choice["name"], choice["sha256"], "Node")
        except DownloadError as exc:
            if not exc.missing:
                raise  # the host itself failed: the other archive is on the same host
            problems.append(str(exc))
            continue
        install_unpacked(archive, home, unpack_tar)
        archive.unlink()
        if not node_works(home, spec["version"]):
            raise ToolError(f"Node {spec['version']} was unpacked into {tilde(home)} but doesn't run")
        return home, True
    raise ToolError("; ".join(problems) or "no Node archive this Mac can unpack")


def lzma_available() -> bool:
    try:
        import lzma  # noqa: F401
    except ImportError:
        return False
    return True


_SYSTEM_CA: dict[str, list[str]] = {}


def system_ca_flag(home: Path) -> list[str]:
    """--use-system-ca lets Node trust the keychain's certificates too, e.g. a company proxy's root CA."""
    exe = str(node_exe(home))
    if exe not in _SYSTEM_CA:
        code, _ = run_text([exe, "--use-system-ca", "-e", "0"], timeout=30)
        _SYSTEM_CA[exe] = ["--use-system-ca"] if code == 0 else []
    return _SYSTEM_CA[exe]


def npm_env(home: Path) -> dict:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(home / "bin"), env.get("PATH") or "/usr/bin:/bin:/usr/sbin:/sbin"])
    env.setdefault("GIT_TERMINAL_PROMPT", "0")  # two optional dependencies come from GitHub; never wait for a password
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    env["npm_config_update_notifier"] = "false"
    env["npm_config_fund"] = "false"
    env["npm_config_audit"] = "false"
    return env


def appium_installed(pins: dict, home: Path) -> bool:
    return (home / ".bc-complete").is_file() and appium_entry(pins, home).is_file()


def install_appium_mcp(pins: dict, node: Path) -> tuple[Path, bool]:
    """appium-mcp exactly as the shipped lockfile says, with npm ci --ignore-scripts. (home, installed now)."""
    spec = pins["appium_mcp"]
    home = appium_home(pins)
    if appium_installed(pins, home):
        return home, False
    home.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".staging-{home.name}-", dir=str(home.parent)))
    log = logs_dir() / f"npm-ci-{stamp()}.log"
    try:
        for name in ("package.json", "package-lock.json"):
            shutil.copyfile(lockfile_dir(pins) / name, staging / name)
        command = [str(node_exe(node)), *system_ca_flag(node), str(npm_cli(node)), "ci", "--ignore-scripts",
                   "--no-audit", "--no-fund", "--loglevel=error"]
        say(f"Installing appium-mcp {spec['version']} (about {spec.get('mb', 380)} MB, one or two minutes)…")
        with open(log, "w") as out:
            try:
                done = subprocess.run(command, cwd=str(staging), env=npm_env(node), stdin=subprocess.DEVNULL,
                                      stdout=out, stderr=subprocess.STDOUT, timeout=NPM_SECONDS)
            except subprocess.TimeoutExpired:
                raise ToolError(f"npm ci took over {NPM_SECONDS // 60} minutes; log: {tilde(log)}") from None
        if done.returncode != 0:
            raise ToolError(f"npm ci failed: {tail(log, 3) or 'no output'} (log: {tilde(log)})")
        if not appium_entry(pins, staging).is_file():
            raise ToolError("npm ci finished, but appium-mcp's dist/index.js is missing")
        (staging / ".bc-complete").write_text(json.dumps({"pin": home.name, "installed_at": now()}) + "\n")
        if home.exists():
            remove_tree(home)
        os.rename(staging, home)
        return home, True
    finally:
        if staging.exists():
            remove_tree(staging)


def java_version(home: Any) -> str | None:
    if not home:
        return None
    java = Path(home) / "bin" / "java"
    if not java.is_file() or str(java).startswith("/usr/bin"):
        return None  # /usr/bin/java is macOS's stub, which opens an install dialog
    code, out = run_text([str(java), "-version"], timeout=30)
    found = re.search(r'version "([^"]+)"', out)
    return found.group(1) if code == 0 and found else None


def install_jdk(pins: dict, key: str) -> tuple[Path, bool]:
    spec = pins["jdk"]
    home = jdk_home(pins)
    java = home / "Contents" / "Home"
    if java_version(java):
        return java, False
    choice = (spec.get("files") or {}).get(key, [None])[0]
    if not choice:
        raise ToolError(f"no JDK is pinned for {key}")
    name = urllib.parse.unquote(Path(urllib.parse.urlparse(choice["url"]).path).name)
    say(f"Downloading a JDK (Temurin {spec['version']}, {choice.get('mb', '?')} MB)…")
    archive = download(choice["url"], tools_dir() / ".downloads" / name, choice["sha256"], "JDK")
    install_unpacked(archive, home, unpack_tar)
    archive.unlink()
    if not java_version(java):
        raise ToolError(f"the JDK was unpacked into {tilde(home)} but java doesn't run")
    return java, True


def android_sdk_works(home: Any) -> dict | None:
    if not home:
        return None
    home = Path(home)
    adb = home / "platform-tools" / "adb"
    signers = sorted(home.glob("build-tools/*/apksigner"))
    if not adb.is_file() or not signers:
        return None
    return {"home": str(home), "adb": str(adb), "build_tools": signers[-1].parent.name}


def install_android_sdk(pins: dict) -> tuple[Path, bool]:
    """adb and the build tools, beside each other in bc's own SDK folder. Never touches the Mac's SDK."""
    home = android_home()
    installed = False
    for part in pins["android_sdk"]["parts"]:
        target = home / part["dir"]
        marker = target / ".bc-complete"
        if marker.is_file() and marker.read_text(encoding="utf-8").strip() == part["sha256"]:
            continue
        name = Path(urllib.parse.urlparse(part["url"]).path).name
        say(f"Downloading Android {part['dir']} ({part.get('mb', '?')} MB)…")
        archive = download(part["url"], tools_dir() / ".downloads" / name, part["sha256"], part["dir"])
        install_unpacked(archive, target, unpack_zip)
        archive.unlink()
        marker.write_text(part["sha256"] + "\n")
        installed = True
    if not android_sdk_works(home):
        raise ToolError(f"the Android SDK in {tilde(home)} has no adb or apksigner after installing")
    return home, installed


# ---------------------------------------------------------------------------
# The Mac: Xcode, a JDK, the Android SDK, phones. Read only; never replaced
# ---------------------------------------------------------------------------


def find_xcode() -> dict:
    code, out = run_text(["/usr/bin/xcode-select", "-p"], timeout=15)
    path = out.strip()
    if code != 0 or not path:
        return {"ok": False, "why": "Xcode isn't installed"}
    if "CommandLineTools" in path or ".app/" not in path:
        return {"ok": False, "path": path, "why": "only Xcode's command line tools are selected"}
    xcodebuild = str(Path(path) / "usr" / "bin" / "xcodebuild")
    code, out = run_text([xcodebuild, "-version"], timeout=60)
    version = out.splitlines()[0].strip() if code == 0 and out.strip() else ""
    first_launch, _ = run_text([xcodebuild, "-checkFirstLaunchStatus"], timeout=60)
    return {"ok": True, "path": path, "version": version, "first_launch_done": first_launch == 0}


def find_java() -> dict | None:
    candidates = []
    if os.environ.get("JAVA_HOME"):
        candidates.append(("JAVA_HOME", os.environ["JAVA_HOME"]))
    code, out = run_text(["/usr/libexec/java_home"], timeout=15)
    if code == 0 and out.strip() and "Unable" not in out:
        candidates.append(("the Mac's default JDK", out.strip().splitlines()[-1]))
    java = shutil.which("java")
    if java and not java.startswith("/usr/bin/"):
        candidates.append(("PATH", str(Path(java).resolve().parent.parent)))
    for source, home in candidates:
        version = java_version(home)
        if version:
            return {"home": home, "version": version, "source": source}
    return None


def find_android_sdk() -> dict | None:
    candidates = [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"), str(Path.home() / "Library" / "Android" / "sdk")]
    adb = shutil.which("adb")
    if adb:
        candidates.append(str(Path(adb).resolve().parent.parent))
    for home in candidates:
        found = android_sdk_works(home)
        if found:
            return found
    return None


def ios_phones() -> list[dict]:
    """Physical iPhones and iPads Xcode knows of. Only a USB phone counts as connected, as for the project's tests."""
    phones: list[dict] = []
    if not Path("/usr/bin/xcrun").exists():
        return phones
    handle, report = tempfile.mkstemp(suffix=".json")
    os.close(handle)
    try:
        code, _ = run_text(["/usr/bin/xcrun", "devicectl", "list", "devices", "--json-output", report, "--quiet"], timeout=60)
        data = read_json(Path(report), {}) if code == 0 else {}
    except ValueError:
        data = {}
    finally:
        with contextlib.suppress(OSError):
            os.unlink(report)
    usb: set[str] = set()
    if shutil.which("idevice_id"):
        code, out = run_text(["idevice_id", "-l"], timeout=20)
        usb = {line.strip() for line in out.splitlines() if code == 0 and line.strip()}
    for device in (data.get("result") or {}).get("devices") or []:
        hardware = device.get("hardwareProperties") or {}
        props = device.get("deviceProperties") or {}
        link = device.get("connectionProperties") or {}
        if hardware.get("reality") != "physical" or hardware.get("platform") not in ("iOS", "iPadOS"):
            continue
        udid = hardware.get("udid") or ""
        wired = link.get("transportType") == "wired" or udid in usb
        if wired and link.get("pairingState") not in (None, "paired"):
            state = "untrusted"
        elif wired:
            state = "connected"
        elif link.get("transportType") == "localNetwork":
            state = "wifi"
        else:
            state = "unplugged"
        phones.append({"platform": "ios", "udid": udid, "name": props.get("name") or hardware.get("deviceType") or "iPhone",
                       "os": props.get("osVersionNumber"), "state": state, "developer_mode": props.get("developerModeStatus")})
    known = {phone["udid"] for phone in phones}
    for udid in sorted(usb - known):
        phones.append({"platform": "ios", "udid": udid, "name": "iPhone", "os": None, "state": "connected", "developer_mode": None})
    return phones


def android_phones(adb: str | None) -> list[dict]:
    if not adb:
        return []
    code, out = run_text([adb, "devices", "-l"], timeout=30)
    phones = []
    for line in out.splitlines()[1:] if code == 0 else []:
        parts = line.split()
        if len(parts) < 2 or parts[0].startswith("emulator-"):
            continue
        model = next((part.split(":", 1)[1] for part in parts[2:] if part.startswith("model:")), "Android phone")
        state = {"device": "connected", "unauthorized": "untrusted"}.get(parts[1], parts[1])
        phones.append({"platform": "android", "udid": parts[0], "name": model.replace("_", " "), "os": None,
                       "state": state, "developer_mode": None})
    return phones


def choose_phone(platforms: list[str], phones: list[dict], udid_setting: str | None) -> dict:
    """The phone the proof runs on: the project's UDID setting when it is set, else the first connected one."""
    if udid_setting and os.environ.get(udid_setting):
        udid = os.environ[udid_setting]
        match = next((phone for phone in phones if phone["udid"] == udid), None)
        if match and match["state"] == "connected":
            return {**match, "source": udid_setting}
        if match:
            return {"why": f"the phone in {udid_setting} ({match['name']}) {state_words(match)}", "udid_setting": udid_setting}
        return {"why": f"the phone in {udid_setting} ({udid}) isn't connected over USB", "udid_setting": udid_setting}
    for wanted in platforms:
        for phone in phones:
            if phone["platform"] == wanted and phone["state"] == "connected":
                return {**phone, "source": "connected over USB"}
    relevant = [phone for phone in phones if phone["platform"] in platforms]
    for phone in relevant:
        if phone["state"] in ("untrusted", "wifi", "offline"):
            return {"why": f"{phone['name']} {state_words(phone)}", "udid_setting": udid_setting}
    return {"why": "no phone is connected over USB", "udid_setting": udid_setting}


def state_words(phone: dict) -> str:
    return {
        "untrusted": "needs you to unlock it and tap Trust" if phone["platform"] == "ios" else "needs you to allow USB debugging on it",
        "wifi": "is connected over Wi-Fi only; plug it in with a USB cable",
        "offline": "is offline; unplug it and plug it in again",
        "unplugged": "isn't connected over USB",
    }.get(phone["state"], f"is {phone['state']}")


# ---------------------------------------------------------------------------
# The test project. Read only: nothing in it is run
# ---------------------------------------------------------------------------


def project_files(root: Path, limit: int = SCAN_LIMIT) -> list[Path]:
    found: list[Path] = []
    base = len(root.parts)
    for folder, dirs, files in os.walk(root):
        depth = len(Path(folder).parts) - base
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(".") and depth < 10)
        for name in sorted(files):
            found.append(Path(folder) / name)
            if len(found) >= limit:
                return found
    return found


def head(path: Path, size: int = HEAD_BYTES) -> str:
    try:
        with open(path, "rb") as handle:
            return handle.read(size).decode("utf-8", errors="replace")
    except OSError:
        return ""


def properties(path: Path) -> dict:
    values = {}
    for line in head(path, 200_000).splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "!")) and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def json_file(path: Path) -> Any:
    try:
        return json.loads(head(path, 2_000_000))
    except ValueError:
        return None


IOS_MARKS = re.compile(r"iOSXCUITFindBy|XCUITest|XCUIElementType|platformName['\"]?\s*[:=,]\s*['\"]iOS|\.ipa\b", re.I)
ANDROID_MARKS = re.compile(r"AndroidFindBy|UiAutomator2|android\.widget\.|platformName['\"]?\s*[:=,]\s*['\"]Android|\.apk\b|appPackage", re.I)
RUNNERS = (("org.testng", "TestNG"), ("io.cucumber", "Cucumber"), ("org.junit", "JUnit"), ("pytest", "pytest"))
WDIO_RUNNERS = (("@wdio/mocha-framework", "Mocha"), ("@wdio/jasmine-framework", "Jasmine"), ("@wdio/cucumber-framework", "Cucumber"))


def read_stack(root: Path, files: list[Path]) -> dict | None:
    """What the tests are written in, from build files and imports. None when this isn't an Appium or WebdriverIO project."""
    package = json_file(root / "package.json") if (root / "package.json").is_file() else None
    deps: dict = {}
    if isinstance(package, dict):
        for section in ("dependencies", "devDependencies"):
            if isinstance(package.get(section), dict):
                deps.update(package[section])
    if "webdriverio" in deps or any(name.startswith("@wdio/") for name in deps):
        language = "TypeScript" if (root / "tsconfig.json").is_file() or "typescript" in deps else "JavaScript"
        runner = next((label for name, label in WDIO_RUNNERS if name in deps), None)
        return {"framework": "WebdriverIO", "language": language, "build": "npm", "runner": runner,
                "evidence": "package.json"}
    build = next((label for name, label in (("build.gradle", "Gradle"), ("build.gradle.kts", "Gradle"), ("pom.xml", "Maven"))
                  if (root / name).is_file()), None)
    evidence = None
    language = None
    runner_text = ""
    for name in ("build.gradle", "build.gradle.kts", "pom.xml"):
        text = head(root / name, 400_000)
        runner_text += text
        if "io.appium" in text:
            evidence, language = name, ("Kotlin" if name.endswith(".kts") else "Java")
    for path in files:
        suffix = path.suffix
        if suffix not in (".java", ".kt", ".py", ".cs", ".rb", ".js", ".ts"):
            continue
        text = head(path)
        if suffix in (".java", ".kt") and re.search(r"^\s*import\s+io\.appium", text, re.M):
            evidence = evidence or str(path.relative_to(root))
            language = language or ("Kotlin" if suffix == ".kt" else "Java")
            runner_text += text
            if evidence and len(runner_text) > 200_000:
                break
        elif suffix == ".py" and re.search(r"^\s*(from|import)\s+appium\b", text, re.M):
            return {"framework": "Appium", "language": "Python", "build": "pip",
                    "runner": "pytest" if "pytest" in text else None, "evidence": str(path.relative_to(root))}
        elif suffix == ".cs" and "OpenQA.Selenium.Appium" in text:
            return {"framework": "Appium", "language": "C#", "build": "dotnet", "runner": None,
                    "evidence": str(path.relative_to(root))}
        elif suffix == ".rb" and "appium_lib" in text:
            return {"framework": "Appium", "language": "Ruby", "build": "bundler", "runner": None,
                    "evidence": str(path.relative_to(root))}
    if not evidence:
        return None
    runner = next((label for name, label in RUNNERS if name in runner_text), None)
    return {"framework": "Appium", "language": language or "Java", "build": build, "runner": runner, "evidence": evidence}


APP_FILE = re.compile(r"\.(ipa|app|apk|aab)/?$", re.I)
APP_ID = re.compile(r"^[A-Za-z][\w-]*(\.[\w-]+)+$")


def app_entry(root: Path, location: str, app_id: Any, source: Path) -> dict | None:
    """An app build named in the project's config; placeholders such as "location to app" are skipped."""
    if not APP_FILE.search(location.strip()):
        return None
    full = Path(os.path.expanduser(location.strip()))
    full = full if full.is_absolute() else root / full
    return {"path": str(full), "exists": full.exists(), "id": app_id if isinstance(app_id, str) and APP_ID.match(app_id) else None,
            "from": str(source.relative_to(root))}


def read_apps(root: Path, files: list[Path]) -> tuple[dict, list[str]]:
    """The app builds the tests install, by platform, and the platforms they name. A local config wins over a hub one."""
    apps: dict[str, dict] = {}
    platforms: list[str] = []
    configs = [path for path in files if path.suffix == ".json" and "environment" in path.name.lower() and "config" in path.name.lower()]
    configs.sort(key=lambda path: (0 if "local" in str(path.relative_to(root)).lower() else 1, str(path)))
    for path in configs:
        data = json_file(path)
        entries = data.get("Apps") if isinstance(data, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            platform = str(entry.get("platform") or "").lower()
            if platform not in ("ios", "android"):
                continue
            platforms.append(platform)
            found = app_entry(root, str(entry.get("location") or ""), entry.get("appPackage"), path)
            if found and platform not in apps:
                apps[platform] = found
    for path in files:
        if not re.search(r"(wdio.*\.conf\.[jt]s|cap.*\.json)$", path.name, re.I):
            continue
        for match in re.finditer(r"['\"]?appium:app['\"]?\s*[:=]\s*['\"]([^'\"]+)['\"]", head(path, 200_000)):
            location = match.group(1)
            platform = "ios" if re.search(r"\.(ipa|app)/?$", location, re.I) else "android" if re.search(r"\.(apk|aab)$", location, re.I) else ""
            found = app_entry(root, location, None, path) if platform else None
            if found and platform not in apps:
                apps[platform] = found
                platforms.append(platform)
    return apps, platforms


def read_platforms(root: Path, files: list[Path], named: list[str], toolchain: dict) -> list[str]:
    counts = {"ios": named.count("ios"), "android": named.count("android")}
    if toolchain.get("appium.driver.xcuitest"):
        counts["ios"] += 1
    if toolchain.get("appium.driver.uiautomator2"):
        counts["android"] += 1
    read = 0
    for path in files:
        if path.suffix not in (".java", ".kt", ".py", ".ts", ".js", ".json", ".properties"):
            continue
        text = head(path, 65_536)
        counts["ios"] += len(IOS_MARKS.findall(text))
        counts["android"] += len(ANDROID_MARKS.findall(text))
        read += 1
        if read >= 600:
            break
    return [platform for platform in ("ios", "android") if counts[platform]]


def read_language(root: Path, files: list[Path]) -> dict:
    execution = root / "execution.properties"
    if execution.is_file():
        value = properties(execution).get("executionLocale", "")
        if value:
            return {"value": value, "from": "execution.properties"}
    for path in files:
        if re.search(r"(wdio.*\.conf\.[jt]s|cap.*\.json)$", path.name, re.I):
            found = re.search(r"['\"]?appium:(?:locale|language)['\"]?\s*[:=]\s*['\"]([^'\"]+)['\"]", head(path, 200_000))
            if found:
                return {"value": found.group(1), "from": str(path.relative_to(root))}
    return {"value": None, "from": None}


def find_setup_script(root: Path) -> dict | None:
    """The project's own setup script, when it has a check-only mode (--check)."""
    for name in SETUP_SCRIPTS:
        path = root / name
        real = path.resolve()
        if not path.is_file() or (real != root.resolve() and root.resolve() not in real.parents):
            continue
        if not os.access(path, os.X_OK):
            continue
        text = head(path, 200_000)
        if not re.search(r"--check\b", text):
            continue
        sources = [text]
        for helper in sorted((root / "scripts").glob("setup/**/*.sh"))[:40]:
            sources.append(head(helper, 100_000))
        with contextlib.suppress(OSError):
            sources.append(head(root / "README.md", 200_000))
        settings = re.findall(r"\b([A-Z][A-Z0-9]*_UDID)\b", "\n".join(sources))
        return {"path": name, "check": "--check", "udid_setting": settings[0] if settings else None}
    return None


def installed_drivers(appium: str) -> dict:
    code, out = run_text([appium, "driver", "list", "--installed", "--json"], timeout=90)
    if code != 0:
        return {}
    try:
        data = json.loads(out[out.index("{"):])
    except ValueError:
        return {}
    return {name: str(info.get("version") or "") for name, info in data.items() if isinstance(info, dict) and info.get("installed", True)}


def read_driver(platforms: list[str], toolchain: dict) -> dict:
    """The project's own Appium server when this Mac has it with the drivers the tests need; else appium-mcp's own."""
    appium = shutil.which("appium")
    if not appium:
        return {"mode": "built-in", "why": "this Mac has no appium command for the project's tests"}
    code, out = run_text([appium, "--version"], timeout=60)
    version = out.strip().splitlines()[-1].strip() if code == 0 and out.strip() else ""
    if not re.match(r"^\d+\.\d+", version):
        return {"mode": "built-in", "why": f"{appium} --version didn't answer"}
    drivers = installed_drivers(appium)
    missing = [PHONE_DRIVERS[p] for p in platforms if PHONE_DRIVERS[p] not in drivers]
    found = {"appium": appium, "version": version, "drivers": {name: drivers[name] for name in sorted(drivers)}}
    expected = {key: value for key, value in (("appium", toolchain.get("appium")),
                                              ("xcuitest", toolchain.get("appium.driver.xcuitest")),
                                              ("uiautomator2", toolchain.get("appium.driver.uiautomator2"))) if value}
    if expected:
        found["expected"] = expected
    if missing:
        return {"mode": "built-in", **found, "why": f"the project's Appium has no {' or '.join(missing)} driver"}
    return {"mode": "project", **found}


def detect(root: Path, pins: dict, key: str | None, deep: bool = True) -> dict:
    files = project_files(root)
    stack = read_stack(root, files)
    if not stack:
        return {"test_project": False, "project": str(root)}
    toolchain_path = root / "toolchain.properties"
    toolchain = properties(toolchain_path) if toolchain_path.is_file() else {}
    apps, named = read_apps(root, files)
    platforms = read_platforms(root, files, named, toolchain)
    found: dict[str, Any] = {
        "test_project": True, "project": str(root), "stack": stack, "platforms": platforms,
        "applies": bool(platforms), "apps": apps, "language": read_language(root, files),
        "setup_script": find_setup_script(root), "toolchain": toolchain or None,
    }
    if not platforms:
        found["why"] = "its tests name no iPhone or Android app, so there is nothing for appium-mcp to drive"
        return found
    if not key:
        found.update(applies=False, why="bc sets up the Plugin Tools on macOS only, for now")
        return found
    if not deep:
        return found
    driver = read_driver(platforms, toolchain)
    mac = {"xcode": find_xcode() if "ios" in platforms else None, "java": find_java(), "android_sdk": find_android_sdk()}
    adb = (mac["android_sdk"] or {}).get("adb") or shutil.which("adb")
    has_xcode = bool((mac["xcode"] or {}).get("ok"))  # without Xcode, xcrun would offer to install it in a dialog
    phones = (ios_phones() if "ios" in platforms and has_xcode else []) + (android_phones(adb) if "android" in platforms else [])
    script = found["setup_script"] or {}
    phone = choose_phone(platforms, phones, script.get("udid_setting"))
    found.update(driver=driver, mac=mac, phones=phones, phone=phone)
    found["plan"] = install_plan(pins, key, driver, platforms, mac)
    found["guided"] = guided_steps(platforms, driver, mac, phone)
    found["lines"] = recap_lines(found, pins)
    return found


def install_plan(pins: dict, key: str, driver: dict, platforms: list[str], mac: dict) -> dict:
    node_ok = node_works(node_home(pins, key), pins["node"]["version"])
    app_ok = appium_installed(pins, appium_home(pins))
    shared = []
    if driver["mode"] == "built-in" and "android" in platforms:
        if not mac["java"] and not java_version(jdk_home(pins) / "Contents" / "Home"):
            shared.append("jdk")
        if not mac["android_sdk"] and not android_sdk_works(android_home()):
            shared.append("android-sdk")
    mb = (0 if node_ok else int(pins["node"].get("disk_mb") or 0)) + (0 if app_ok else int(pins["appium_mcp"].get("disk_mb") or 0))
    mb += int(pins.get("jdk", {}).get("disk_mb") or 0) if "jdk" in shared else 0
    mb += int(pins.get("android_sdk", {}).get("disk_mb") or 0) if "android-sdk" in shared else 0
    current = read_server_record()
    current_app = next((Path(arg) for arg in (current or {}).get("args") or [] if str(arg).endswith(".js")), None)
    upgrade_from = None
    if current_app and tools_dir() in current_app.parents and appium_home(pins) not in current_app.parents:
        upgrade_from = next((part for part in current_app.parts if re.match(r"^\d+\.\d+\.\d+-[0-9a-f]{12}$", part)), None)
    return {"node": {"version": pins["node"]["version"], "installed": node_ok},
            "appium_mcp": {"version": pins["appium_mcp"]["version"], "installed": app_ok, "upgrade_from": upgrade_from},
            "shared": shared, "mb": mb}


def guided_steps(platforms: list[str], driver: dict, mac: dict, phone: dict) -> list[str]:
    """What only a person can do: sudo, the App Store, signing, and the phone."""
    steps = []
    xcode = mac.get("xcode")
    if "ios" in platforms and xcode is not None:
        if not xcode["ok"] and "isn't installed" in xcode.get("why", ""):
            steps.append("install Xcode from the App Store and open it once")
        elif not xcode["ok"]:
            steps.append("select Xcode: run `sudo xcode-select -s /Applications/Xcode.app` in a terminal (it asks for your Mac password)")
        elif not xcode.get("first_launch_done"):
            steps.append("finish Xcode's setup: run `sudo xcodebuild -runFirstLaunch` in a terminal (it asks for your Mac password)")
        if driver["mode"] == "built-in":
            steps.append("sign WebDriverAgent for your iPhone with a provisioning profile you choose (the project's own Appium setup avoids this)")
    if phone.get("udid") and phone.get("platform") == "ios" and phone.get("developer_mode") == "disabled":
        steps.append(f"turn on Developer Mode on {phone['name']}: Settings → Privacy & Security → Developer Mode")
    if not phone.get("udid"):
        steps.append(phone_hint(phone, platforms))
    return steps


def phone_hint(phone: dict, platforms: list[str]) -> str:
    why = phone.get("why") or "no phone is connected over USB"
    if why == "no phone is connected over USB":
        trust = "tap Trust" if "ios" in platforms else "allow USB debugging"
        return f"no phone is connected over USB yet: plug one in, unlock it and {trust}; setup checks again on its next run"
    return f"{why}; setup checks again on its next run"


def recap_lines(found: dict, pins: dict) -> list[str]:
    stack = found["stack"]
    details = ", ".join(part for part in (stack["language"], stack.get("runner"), stack.get("build")) if part)
    names = " and ".join({"ios": "iOS", "android": "Android"}[p] for p in found["platforms"])
    pad = " " * 15
    lines = [f"Automation:    {stack['framework']} test project ({details}) — {names}"]
    driver = found["driver"]
    if driver["mode"] == "project":
        drivers = ", ".join(f"{name} {version}" for name, version in driver["drivers"].items() if name in PHONE_DRIVERS.values())
        lines.append(f"{pad}drives the phone through the project's own Appium {driver['version']} ({drivers})")
        expected = driver.get("expected") or {}
        if expected.get("appium") and expected["appium"] != driver["version"]:
            lines.append(f"{pad}(the project expects Appium {expected['appium']}; its own setup fixes that)")
    else:
        lines.append(f"{pad}drives the phone with appium-mcp's built-in drivers — {driver['why']}")
    phone = found["phone"]
    phone_words = f"{phone['name']} ({phone['source']})" if phone.get("udid") else "none connected yet"
    app = found["apps"].get(phone.get("platform") or "") or next(iter(found["apps"].values()), None)
    app_words = (tilde(app["path"]) + ("" if app["exists"] else " (not on this Mac yet)")) if app else "none named in the project"
    language = found["language"]["value"] or "the phone's own"
    lines.append(f"{pad}phone: {phone_words} · app: {app_words} · language: {language}")
    plan = found["plan"]
    parts = []
    if not plan["node"]["installed"]:
        parts.append(f"Node {plan['node']['version']}")
    if not plan["appium_mcp"]["installed"]:
        upgrade = plan["appium_mcp"].get("upgrade_from")
        parts.append(f"appium-mcp {plan['appium_mcp']['version']}" + (f" (replacing {upgrade.split('-')[0]})" if upgrade else ""))
    if "jdk" in plan["shared"]:
        parts.append(f"a JDK (Temurin {pins['jdk']['version']})")
    if "android-sdk" in plan["shared"]:
        parts.append("Android's adb and build tools (saying yes accepts Google's Android SDK license)")
    if parts:
        lines.append(f"{pad}installs bc's own {', '.join(parts[:-1]) + ' and ' + parts[-1] if len(parts) > 1 else parts[0]}"
                     f" into {tilde(tools_dir())} — about {plan['mb']} MB on disk, never on PATH")
    else:
        lines.append(f"{pad}bc's own Node {plan['node']['version']} and appium-mcp {plan['appium_mcp']['version']} are already installed")
    for step in found["guided"]:
        lines.append(f"{pad}needs you: {step}")
    return lines


# ---------------------------------------------------------------------------
# The record: how the plugin's appium connector (scripts/bc-mcp → serve) starts bc's appium-mcp
# ---------------------------------------------------------------------------


def server_entry(pins: dict, node: Path, app: Path, extra_env: dict) -> dict:
    env = {
        "NO_UI": "true",
        "ALLOW_REMOTE_APP_URLS": "false",
        "REMOTE_SERVER_URL_ALLOW_REGEX": LOCAL_ONLY_URLS,
        "PAGE_SOURCES_DIR": str(work_dir() / "page-sources"),
        "SCREENSHOTS_DIR": str(work_dir() / "screenshots"),
    }
    env.update(extra_env)
    return {"command": str(node_exe(node)), "args": [*system_ca_flag(node), str(appium_entry(pins, app))], "env": env}


def bc_owned(entry: Any) -> bool:
    """Whether an entry runs bc's own Node — the only kind the record ever holds."""
    command = str((entry or {}).get("command") or "") if isinstance(entry, dict) else ""
    return command.startswith(str(tools_dir()) + os.sep)


def read_server_record() -> dict | None:
    """What the appium connector starts, as the last ensure whose server answered recorded it."""
    with contextlib.suppress(ValueError, OSError):
        entry = read_json(server_record(), None)
        if isinstance(entry, dict) and entry.get("command"):
            return entry
    return None


def save_server_record(entry: dict) -> list[str]:
    """Record how the connector starts appium-mcp. Returns the record's path when it changed."""
    if read_server_record() == entry:
        return []
    write_json(server_record(), entry, 0o644)
    return [tilde(server_record())]


def drop_server_record() -> list[str]:
    path = server_record()
    if not path.exists():
        return []
    path.unlink()
    return [tilde(path)]


def entry_exists(entry: dict | None) -> bool:
    if not entry or not Path(str(entry.get("command", ""))).is_file():
        return False
    scripts = [arg for arg in entry.get("args") or [] if str(arg).endswith(".js")]
    return all(Path(str(script)).is_file() for script in scripts)


def load_state() -> dict:
    with contextlib.suppress(ValueError):
        return read_json(tools_dir() / "state.json", {}) or {}
    return {}


def save_state(**updates: Any) -> None:
    data = load_state()
    data.update(updates)
    data["updated_at"] = now()
    write_json(tools_dir() / "state.json", data, 0o644)


def age_seconds(path: Path) -> float:
    try:
        return time.time() - path.lstat().st_mtime
    except OSError:
        return 0.0


def prune(keep: Iterable[Any] = ()) -> list[str]:
    """Remove pins nothing points at, unfinished downloads, week-old screen files, and old logs.

    A pin folder stays when anything kept lives inside it — a kept path, or a command, argument, or env value of
    the recorded entry the appium connector starts (JAVA_HOME points inside a JDK folder, not at it).
    """
    kept: set[Path] = set()

    def hold(item: Any) -> None:
        path = Path(str(item or ""))
        if path.is_absolute():
            kept.update(place.resolve() for place in [path, *path.parents])

    for item in keep:
        hold(item)
    entry = read_server_record() or {}
    for item in [entry.get("command"), *(entry.get("args") or []), *((entry.get("env") or {}).values())]:
        hold(item)
    removed = []
    for family in ("node", "appium-mcp", "jdk"):
        base = tools_dir() / family
        for child in sorted(base.iterdir()) if base.is_dir() else []:
            if child.resolve() in kept or (child.name.startswith(".staging-") and age_seconds(child) < 3600):
                continue
            remove_tree(child)
            removed.append(tilde(child))
    downloads = tools_dir() / ".downloads"
    for child in sorted(downloads.iterdir()) if downloads.is_dir() else []:
        if age_seconds(child) > 3600:
            remove_tree(child)
    for folder in (work_dir() / "page-sources", work_dir() / "screenshots"):
        for child in sorted(folder.iterdir()) if folder.is_dir() else []:
            if age_seconds(child) > KEEP_FILES_DAYS * 86400:
                remove_tree(child)
    logs = sorted(logs_dir().iterdir(), key=age_seconds)
    for old in logs[KEEP_LOGS:]:
        remove_tree(old)
    return removed


# ---------------------------------------------------------------------------
# The proof: talk to the server the way a CLI does (MCP over stdio), then read one screen from the phone
# ---------------------------------------------------------------------------


class McpClient:
    """A minimal MCP client over stdio: newline-delimited JSON-RPC 2.0."""

    def __init__(self, entry: dict, log: Path):
        self.entry, self.log = entry, log
        self.messages: queue.Queue = queue.Queue()
        self.next_id = 0
        self.process: subprocess.Popen | None = None
        self.errors = None

    def __enter__(self) -> "McpClient":
        env = dict(os.environ)
        env.update(self.entry.get("env") or {})
        self.errors = open(self.log, "ab")
        self.process = subprocess.Popen(
            [self.entry["command"], *self.entry.get("args", [])], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.errors, env=env, start_new_session=True,
        )
        threading.Thread(target=self._read, daemon=True).start()
        return self

    def _read(self) -> None:
        assert self.process and self.process.stdout
        for raw in self.process.stdout:
            self.messages.put(raw)
        self.messages.put(None)

    def _send(self, message: dict) -> None:
        assert self.process and self.process.stdin
        try:
            self.process.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            raise ToolError(f"the appium-mcp server stopped; its log: {tilde(self.log)} — {tail(self.log, 2)}") from None

    def request(self, method: str, params: dict, timeout: float) -> dict:
        self.next_id += 1
        ident = self.next_id
        self._send({"jsonrpc": "2.0", "id": ident, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise ToolError(f"appium-mcp gave no answer to {method} within {int(timeout)} s")
            try:
                raw = self.messages.get(timeout=min(left, 1.0))
            except queue.Empty:
                continue
            if raw is None:
                raise ToolError(f"the appium-mcp server stopped; its log: {tilde(self.log)} — {tail(self.log, 2)}")
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if "method" in message and "id" in message:  # a request from the server, such as ping
                answer = {"result": {}} if message["method"] == "ping" else {"error": {"code": -32601, "message": "not supported"}}
                self._send({"jsonrpc": "2.0", "id": message["id"], **answer})
                continue
            if message.get("id") != ident:
                continue
            if "error" in message:
                raise ToolError(f"appium-mcp refused {method}: {(message['error'] or {}).get('message')}")
            return message.get("result") or {}

    def handshake(self) -> dict:
        started = time.monotonic()
        result = self.request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "buddy-council-setup", "version": plugin_version()},
        }, HANDSHAKE_SECONDS)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools: list[str] = []
        cursor = None
        while True:
            page = self.request("tools/list", {"cursor": cursor} if cursor else {}, 60)
            tools.extend(str(tool.get("name")) for tool in page.get("tools") or [])
            cursor = page.get("nextCursor")
            if not cursor:
                break
        info = result.get("serverInfo") or {}
        return {"seconds": round(time.monotonic() - started, 1), "version": info.get("version"), "tools": tools}

    def call_tool(self, name: str, arguments: dict, timeout: float) -> tuple[bool, str]:
        result = self.request("tools/call", {"name": name, "arguments": arguments}, timeout)
        text = "\n".join(part.get("text", "") for part in result.get("content") or [] if part.get("type") == "text")
        return not result.get("isError"), text

    def __exit__(self, *exc: Any) -> None:
        if self.process:
            with contextlib.suppress(OSError):
                self.process.stdin.close()  # type: ignore[union-attr]
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                stop_group(self.process.pid)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self.process.wait(timeout=5)
            with contextlib.suppress(OSError):
                self.process.stdout.close()  # type: ignore[union-attr]
        if self.errors:
            self.errors.close()


def stop_group(pid: int) -> None:
    for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, sig)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            with contextlib.suppress(ChildProcessError):
                if os.waitpid(pid, os.WNOHANG)[0] == pid:
                    return
            time.sleep(0.2)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def server_ready(url: str) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"{url}/status", timeout=5) as response:
            value = json.loads(response.read().decode("utf-8")).get("value") or {}
            return bool(value.get("ready", True))
    except (OSError, ValueError, urllib.error.URLError):
        return False


def server_file() -> Path:
    return work_dir() / "server.json"


_SERVERS: dict[int, subprocess.Popen] = {}  # servers this process started, so stopping one also reaps it


def start_server(appium: str) -> dict:
    """The project's own Appium on a free port of 127.0.0.1, in its own process group. Returns its url."""
    port = free_port()
    log = logs_dir() / f"appium-{stamp()}.log"
    url = f"http://127.0.0.1:{port}"
    with open(log, "w") as out:
        process = subprocess.Popen([appium, "--address", "127.0.0.1", "--port", str(port), "--log-no-colors"],
                                   stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    _SERVERS[process.pid] = process
    info = {"pid": process.pid, "url": url, "appium": appium, "log": str(log), "started_at": now()}
    write_json(server_file(), info, 0o600)
    deadline = time.monotonic() + SERVER_START_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _SERVERS.pop(process.pid, None)
            server_file().unlink()
            raise ToolError(f"the project's Appium stopped while starting: {tail(log, 2)} (log: {tilde(log)})")
        if server_ready(url):
            return info
        time.sleep(0.5)
    stop_server(info)
    raise ToolError(f"the project's Appium didn't start within {SERVER_START_SECONDS} s (log: {tilde(log)})")


def stop_server(info: dict | None = None) -> bool:
    info = info or read_json(server_file(), None)
    if not info:
        return False
    pid = int(info.get("pid") or 0)
    code, out = run_text(["/bin/ps", "-o", "command=", "-p", str(pid)], timeout=10)
    if pid and code == 0 and "appium" in out:
        stop_group(pid)
    process = _SERVERS.pop(pid, None)
    if process:
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)
    with contextlib.suppress(OSError):
        server_file().unlink()
    return True


def phone_capabilities(phone: dict) -> dict:
    caps: dict[str, Any] = {"appium:udid": phone["udid"], "appium:deviceName": phone["name"], "appium:noReset": True,
                            "appium:newCommandTimeout": 120}
    if phone["platform"] == "ios":
        caps.update({"platformName": "iOS", "appium:automationName": "XCUITest", "appium:wdaLaunchTimeout": 240000})
    else:
        caps.update({"platformName": "Android", "appium:automationName": "UiAutomator2"})
    return caps


def read_screen(client: McpClient, found: dict) -> dict:
    """Open a session on the phone without launching any app, save the screen's page source, close the session."""
    phone = found["phone"]
    if not phone.get("udid"):
        return {"ok": False, "no_phone": True, "pending": phone_hint(phone, found["platforms"])}
    arguments: dict[str, Any] = {"action": "create", "platform": phone["platform"],
                                 "capabilities": json.dumps(phone_capabilities(phone))}
    server = None
    try:
        if found["driver"]["mode"] == "project":
            server = start_server(found["driver"]["appium"])
            arguments["remoteServerUrl"] = server["url"]
        say(f"Opening a session on {phone['name']} (the first one on an iPhone builds WebDriverAgent, up to a few minutes)…")
        ok, text = client.call_tool("appium_session_management", arguments, SESSION_SECONDS)
        if not ok:
            return {"ok": False, "pending": session_hint(text, found)}
        ok, text = client.call_tool("appium_get_page_source", {"saveToFile": True}, 90)
        saved: dict = {}
        with contextlib.suppress(ValueError):
            saved = json.loads(text[text.index("{"):]) if "{" in text else {}
        with contextlib.suppress(ToolError):
            client.call_tool("appium_session_management", {"action": "delete"}, 30)
        if not ok or not saved.get("filepath"):
            return {"ok": False, "pending": f"the session opened on {phone['name']}, but the page source wasn't saved: {one_line(text)}"}
        return {"ok": True, "phone": phone["name"], "platform": phone["platform"], "page_source": saved["filepath"],
                "bytes": int(saved.get("bytes") or 0)}
    except ToolError as exc:
        return {"ok": False, "pending": str(exc)}
    finally:
        if server:
            stop_server(server)


def session_hint(text: str, found: dict) -> str:
    phone = found["phone"]
    reason = one_line(text)
    if re.search(r"xcodebuild|WebDriverAgent|signing|provisioning|code ?65", text, re.I):
        script = (found.get("setup_script") or {}).get("path")
        fix = f"run ./{script} in a terminal — it signs WebDriverAgent for the phone" if script else "WebDriverAgent needs signing for this phone"
        return f"couldn't start WebDriverAgent on {phone['name']}: {fix} ({reason})"
    if re.search(r"locked|passcode|unlock", text, re.I):
        return f"{phone['name']} is locked: unlock it, then run /bc:setup again ({reason})"
    return f"couldn't open a session on {phone['name']}: {reason}"


# ---------------------------------------------------------------------------
# Recording the result in the project's sources.json
# ---------------------------------------------------------------------------


def project_root(text: str) -> Path:
    root = Path(os.path.expanduser(text)).resolve()
    if not root.is_dir():
        raise ToolError(f"{text} isn't a folder")
    return root


def sources_path(root: Path) -> Path:
    return root / ".buddy-council" / "sources.json"


def recorded(root: Path) -> dict:
    try:
        data = read_json(sources_path(root), {}) or {}
    except ValueError:
        return {}
    block = data.get("automation") if isinstance(data, dict) else None
    return block if isinstance(block, dict) else {}


def record(root: Path, block: dict) -> None:
    """Replace the automation block in the project's settings, through bc_setup — which checks it against the
    example settings and saves it — and nothing else in the file."""
    path = sources_path(root)
    if not path.is_file():
        raise ToolError(f"{tilde(path)} doesn't exist yet: save the setup first, then run this again")
    try:
        bc_setup.save(root, {"automation": block}, unset=["automation"], stamp=False)
    except bc_setup.SetupError as exc:
        raise ToolError(f"the result wasn't recorded in {tilde(path)}: {exc}") from None


def valid_proof(block: dict, found: dict) -> dict | None:
    """The last phone check that passed, while it still applies: the same way of driving the phone, and the same
    phone whenever one is connected. A check that later fails drops it."""
    proof = block.get("proof") if isinstance(block.get("proof"), dict) else None
    if not proof or not proof.get("page_source") or proof.get("mode") != found["driver"]["mode"]:
        return None
    phone = found["phone"]
    if phone.get("udid") and phone["udid"] != proof.get("udid"):
        return None
    return proof


def automation_block(found: dict, pins: dict, status: str, pending: list[str], server: dict | None,
                     proof: dict | None, shared: dict, guided: list[str]) -> dict:
    phone = found["phone"]
    driver = {key: value for key, value in found["driver"].items() if key in ("mode", "appium", "version", "drivers", "expected", "why")}
    return {
        "enabled": True,
        "status": status,
        "pending": pending,
        "guided": guided,
        "stack": found["stack"],
        "platforms": found["platforms"],
        "driver": driver,
        "phone": ({key: phone.get(key) for key in ("platform", "udid", "name", "source")} if phone.get("udid")
                  else {"none": phone.get("why"), "udid_setting": phone.get("udid_setting")}),
        "apps": found["apps"],
        "language": found["language"]["value"],
        "setup_script": found["setup_script"],
        "tools": {"node": pins["node"]["version"], "appium_mcp": pins["appium_mcp"]["version"], **shared},
        "server": server,
        "proof": proof,
        "checked_at": now(),
    }


def outcome_lines(pins: dict, status: str, pending: list[str], server: dict | None, proof: dict | None, fresh: bool,
                  phone_check: str, phone_name: str, changes: dict, notes: list[str], guided: list[str]) -> list[str]:
    pad = " " * 15
    answered = (f"appium-mcp {pins['appium_mcp']['version']} answered in {server['answered_in_s']} s ({server['tools']} tools)"
                if server else "")
    rest = pending
    if status == "ready" and proof and fresh:
        first = f"ready — {answered} and saved a page source from {proof['phone']} ({proof['bytes']:,} bytes)"
    elif status == "ready" and proof:
        first = f"ready — {answered}; {proof['phone']} passed the phone check on {str(proof.get('checked_at'))[:10]}"
    elif server and phone_check == "due":
        first, rest = f"{answered}; next, one page source from {phone_name}", pending[1:]
    elif server:
        first, rest = f"pending — {answered}, but {pending[0] if pending else 'the phone check did not finish'}", pending[1:]
    else:
        first, rest = f"pending — {pending[0] if pending else 'the tools are not working yet'}", pending[1:]
    lines = [f"Automation:    {first}"]
    lines.extend(f"{pad}also pending: {item}" for item in rest)
    if changes.get("installed"):
        lines.append(f"{pad}installed into {tilde(tools_dir())}: {', '.join(changes['installed'])}")
    if changes.get("recorded"):
        lines.append(f"{pad}the plugin's appium connector starts this version from now on — restart your assistant "
                     "once to load it")
    if changes.get("removed"):
        lines.append(f"{pad}removed the versions nothing uses any more: {', '.join(Path(item).name for item in changes['removed'])}")
    lines.extend(f"{pad}{note}" for note in notes)
    lines.extend(f"{pad}needs you: {step}" for step in guided if step not in " ".join(pending))
    return lines


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_detect(project: str) -> dict:
    root = project_root(project)
    return detect(root, load_pins(), platform_key())


def cmd_ensure(project: str, shared_text: str = "", install: bool = True, phone_check: bool = False) -> dict:
    """Install or upgrade, prove the server answers, record how the connector starts it, prune, and record the
    result. With phone_check (the smoke subcommand), also read one page source from the phone. Each half fits in one
    10-minute shell call."""
    root = project_root(project)
    pins, key = load_pins(), platform_key()
    wanted = {item.strip() for item in (shared_text or "").split(",") if item.strip()}
    if wanted - {"jdk", "android-sdk"}:
        raise ToolError(f"--shared takes jdk and android-sdk, not {', '.join(sorted(wanted - {'jdk', 'android-sdk'}))}")
    with tools_lock():
        found = detect(root, pins, key)
        if not found["test_project"]:
            raise ToolError(f"{tilde(root)} isn't an Appium or WebdriverIO test project, so there is nothing to set up")
        if not found.get("applies"):
            raise ToolError(str(found.get("why")))
        assert key
        proof = valid_proof(recorded(root), found)
        pending: list[str] = []
        notes: list[str] = []
        changes: dict[str, list[str]] = {"installed": [], "recorded": [], "removed": []}
        hint = phone_hint(found["phone"], found["platforms"])
        guided = [step for step in found["guided"] if not (proof and step == hint)]

        def finish(server: dict | None, fresh: bool, phone_state: str, shared: dict, log: Path | None = None) -> dict:
            status = "ready" if server and proof and not pending else "pending"
            record(root, automation_block(found, pins, status, pending, server, proof, shared, guided))
            return {"status": status, "phone_check": phone_state, "server": server, **changes, "pending": pending,
                    "notes": notes, "guided": guided, "log": tilde(log) if log else None,
                    "lines": outcome_lines(pins, status, pending, server, proof, fresh, phone_state,
                                           found["phone"].get("name") or "the phone", changes, notes, guided)}

        if install:  # recorded first, so a run the CLI stops early still shows as pending and is finished next run
            record(root, automation_block(found, pins, "pending", ["bc's tools are being installed; if this run stopped "
                                          "early, setup finishes it on its next run"], None, proof, {}, guided))
        state = load_state()
        try:
            if install:
                node, fresh_install = install_node(pins, key)
                changes["installed"] += [f"Node {pins['node']['version']}"] if fresh_install else []
                app, fresh_install = install_appium_mcp(pins, node)
                changes["installed"] += [f"appium-mcp {pins['appium_mcp']['version']}"] if fresh_install else []
            else:
                node, app = node_home(pins, key), appium_home(pins)
                if not node_works(node, pins["node"]["version"]) or not appium_installed(pins, app):
                    raise ToolError("the pinned Node and appium-mcp aren't installed yet: run /bc:setup")
        except ToolError as exc:
            pending.append(f"the tools couldn't be installed ({exc}); setup tries again on its next run")
            return finish(None, False, "passed" if proof else "not checked", {})
        extra_env, shared = shared_tools(found, pins, key, wanted, state, changes["installed"], notes, install)
        entry = server_entry(pins, node, app, extra_env)
        before = read_server_record()
        log = logs_dir() / f"appium-mcp-{stamp()}.log"
        server = screen = None
        try:
            with McpClient(entry, log) as client:
                hello = client.handshake()
                missing = [tool for tool in NEEDED_TOOLS if tool not in hello["tools"]]
                if missing:
                    raise ToolError(f"appium-mcp answered without {', '.join(missing)}")
                gone = [tool for tool in READ_TOOLS if tool not in hello["tools"]]
                if gone:
                    notes.append(f"this appium-mcp has no {', '.join(gone)}; the auto-approval hook's list needs updating")
                server = {"answered_in_s": hello["seconds"], "tools": len(hello["tools"]), "version": hello["version"]}
                changes["recorded"] = save_server_record(entry)
                save_state(node=node.name, appium_mcp=app.name, **{name: value for name, value in shared.items() if value})
                changes["removed"] = prune([node, app, extra_env.get("JAVA_HOME"), extra_env.get("ANDROID_HOME")])
                if phone_check:
                    screen = read_screen(client, found)
        except ToolError as exc:
            if server is None and entry_exists(before) and bc_owned(before):
                if before != entry:
                    pending.append(f"the new appium-mcp didn't answer ({exc}); the version before it stays in use, and setup tries again on its next run")
                else:
                    pending.append(f"appium-mcp didn't answer ({exc}); setup tries again on its next run")
            else:
                pending.append(f"appium-mcp didn't work ({exc}); setup tries again on its next run")
        fresh = False
        if screen and screen.get("ok"):
            proof = {"phone": screen["phone"], "udid": found["phone"]["udid"], "platform": screen["platform"],
                     "mode": found["driver"]["mode"], "page_source": screen["page_source"], "bytes": screen["bytes"],
                     "checked_at": now()}
            fresh, phone_state = True, "passed"
            guided = [step for step in guided if step != hint]
        elif screen and not screen.get("no_phone"):
            proof = None  # it failed on the phone that's connected now, so an older pass no longer counts
            pending.append(screen["pending"])
            phone_state = "failed"
        elif proof:
            phone_state = "passed"
        elif not server:
            phone_state = "not checked"
        elif found["phone"].get("udid"):
            pending.append(f"the phone check on {found['phone']['name']} runs next")
            phone_state = "due"
        else:
            pending.append(hint)
            phone_state = "not connected"
        return finish(server, fresh, phone_state, shared, log)


def shared_tools(found: dict, pins: dict, key: str, wanted: set[str], state: dict, installed: list[str],
                 notes: list[str], install: bool) -> tuple[dict, dict]:
    """JAVA_HOME and ANDROID_HOME for the server, only for Android on appium-mcp's built-in drivers.

    The Mac's own JDK and SDK are used when they work. bc's side-by-side copies are used when they don't: an
    installed copy always, and a download only with `install` and after the user said yes (or bc installed one
    before). Neither path is ever set anywhere but the server's env.
    """
    if found["driver"]["mode"] != "built-in" or "android" not in found["platforms"]:
        return {}, {}
    env: dict[str, str] = {}
    shared: dict[str, Any] = {}
    java = (found["mac"]["java"] or {}).get("home")
    sdk = (found["mac"]["android_sdk"] or {}).get("home")
    own_java = jdk_home(pins) / "Contents" / "Home"
    if not java and java_version(own_java):
        java, shared["jdk"] = str(own_java), pins["jdk"]["version"]
    elif not java and install and ("jdk" in wanted or state.get("jdk")):
        try:
            path, fresh = install_jdk(pins, key)
            java, shared["jdk"] = str(path), pins["jdk"]["version"]
            installed += [f"a JDK (Temurin {pins['jdk']['version']})"] if fresh else []
        except ToolError as exc:
            notes.append(f"couldn't install a JDK for Android ({exc}); setup tries again on its next run")
    own_sdk = android_home()
    sdk_parts_current = all((own_sdk / part["dir"] / ".bc-complete").is_file() for part in pins["android_sdk"]["parts"])
    if not sdk and android_sdk_works(own_sdk) and (sdk_parts_current or not install):
        sdk, shared["android_sdk"] = str(own_sdk), pins["android_sdk"]["version"]
    elif not sdk and install and ("android-sdk" in wanted or state.get("android_sdk")):
        try:
            path, fresh = install_android_sdk(pins)
            sdk, shared["android_sdk"] = str(path), pins["android_sdk"]["version"]
            installed += ["Android's adb and build tools"] if fresh else []
        except ToolError as exc:
            notes.append(f"couldn't install Android's adb and build tools ({exc}); setup tries again on its next run")
    if java:
        env["JAVA_HOME"] = java
    if sdk:
        env["ANDROID_HOME"] = sdk
    if not java or not sdk:
        notes.append("Android tests on appium-mcp's built-in drivers need a JDK and the Android SDK; run /bc:setup and say yes to installing them")
    return env, shared


def cmd_status(project: str) -> dict:
    root = project_root(project)
    pins, key = load_pins(), platform_key()
    block = recorded(root)
    if not block:
        found = detect(root, pins, key, deep=False)
        if found["test_project"] and found.get("applies"):
            stack = found["stack"]
            return {"enabled": None, "test_project": True,
                    "line": f"Automation:    not set up — this is an {stack['framework']} ({stack['language']}) test project, "
                            "so automation joins this run's save (say \"leave out automation\" to skip it)"}
        return {"enabled": None, "test_project": found["test_project"], "line": None}
    if block.get("enabled") is False:
        return {"enabled": False, "line": "Automation:    left out — say \"automation\" to add it"}
    needs = []
    if key:
        node, app = node_home(pins, key), appium_home(pins)
        if not node_works(node, pins["node"]["version"]) or not appium_installed(pins, app):
            needs.append("install")
        entry = read_server_record()
        if not entry_exists(entry) or str(node) not in str(entry.get("command")) or not any(str(app) in str(arg) for arg in entry.get("args") or []):
            needs.append("record")
    if block.get("status") != "ready":
        needs.append("retry")
    tools = block.get("tools") or {}
    if block.get("status") == "ready":
        line = f"Automation:    ready — appium-mcp {tools.get('appium_mcp')}, checked {block.get('checked_at')}"
    else:
        reason = (block.get("pending") or ["not checked yet"])[0]
        reason = re.sub(r"\s*[;—-]+\s*setup (checks|tries) again on its next run\.?$", "", reason)
        line = f"Automation:    pending — {reason} (re-checking this run)"
    return {"enabled": True, "status": block.get("status"), "needs": needs, "pending": block.get("pending") or [], "line": line}


REPORT_MARKS = {"problems": re.compile(r"^\s*(❌|✗|✘|FAIL\b|ERROR\b)"), "warnings": re.compile(r"^\s*(⚠️?|WARN\b)")}
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def parse_report(text: str) -> dict:
    rows = [ANSI.sub("", row).rstrip() for row in text.splitlines()]
    report: dict[str, Any] = {"result": None}
    for name, mark in REPORT_MARKS.items():
        report[name] = [mark.sub("", row).strip() for row in rows if mark.match(row)][:12]
    results = [row.split(":", 1)[1].strip() for row in rows if row.startswith("Result:")]
    report["result"] = results[-1] if results else None
    report["tail"] = [row.strip() for row in rows if row.strip()][-8:]
    return report


def cmd_project(project: str, mode: str) -> dict:
    root = project_root(project)
    script = find_setup_script(root)
    if not script:
        raise ToolError(f"{tilde(root)} has no setup script with a --check mode")
    command = [str(root / script["path"])] + (["--check"] if mode == "check" else [])
    shown = f"./{script['path']}" + (" --check" if mode == "check" else "")
    log = logs_dir() / f"project-{mode}-{stamp()}.log"
    say(f"Running the project's {shown} (it can take a few minutes)…")
    env = dict(os.environ)
    env.setdefault("TERM", "dumb")
    with open(log, "w") as out:
        process = subprocess.Popen(command, cwd=str(root), env=env, stdin=subprocess.DEVNULL, stdout=out,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=PROJECT_SECONDS)
        except subprocess.TimeoutExpired:
            stop_group(process.pid)  # the script and everything it started, such as a download
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
            code = 124
    report = parse_report(log.read_text(encoding="utf-8", errors="replace"))
    words = report["result"] or ("passed" if code == 0 else
                                 f"didn't finish within {PROJECT_SECONDS // 60} minutes" if code == 124 else f"stopped with exit code {code}")
    result = {"ok": code == 0, "exit_code": code, "mode": mode, "command": shown, **report,
              "line": f"the project's {shown}: {words}", "log": tilde(log)}
    if code != 0 and (mode == "fix" or code == 124):
        what = "; ".join(report["problems"][:4] or report["tail"][-2:])
        result["needs_you"] = f"run ./{script['path']} in a terminal for what needs you" + (f": {what}" if what else "")
    return result


def cmd_start(project: str) -> dict:
    root = project_root(project)
    found = detect(root, load_pins(), platform_key())
    driver = found.get("driver") or {}
    if driver.get("mode") != "project":
        raise ToolError(f"this project uses appium-mcp's built-in drivers, not an Appium server of its own ({driver.get('why') or found.get('why')})")
    stop_server()
    return start_server(driver["appium"])


def cmd_prune() -> dict:
    pins, key = load_pins(), platform_key()
    with tools_lock():
        state = load_state()
        keep: list[Any] = [tools_dir() / "jdk" / str(state["jdk"])] if state.get("jdk") else []
        if key:
            keep += [node_home(pins, key), appium_home(pins)]
        return {"removed": prune(keep)}


def cmd_remove(project: str) -> dict:
    root = project_root(project)
    with tools_lock():
        stop_server()
        dropped = drop_server_record()
        removed = []
        for name in ("node", "appium-mcp", "jdk", "android-sdk", ".downloads", "state.json"):
            path = tools_dir() / name
            if path.exists():
                remove_tree(path)
                removed.append(tilde(path))
    with contextlib.suppress(ToolError):
        record(root, {"enabled": False, "removed_at": now()})
    return {"record_dropped": dropped, "removed": removed,
            "lines": ["Automation:    removed — bc's tools are deleted, and the plugin's appium connector has no tools "
                      "from your assistant's next start"]}


# ---------------------------------------------------------------------------
# The plugin's appium connector: scripts/bc-mcp appium runs `serve`
# ---------------------------------------------------------------------------

STUB_INSTRUCTIONS = ("bc's appium tools aren't installed on this computer yet. Run /bc:setup in an Appium or "
                     "WebdriverIO test project to install them, then restart the assistant.")


def cmd_serve() -> int:
    """Become the appium-mcp the record names. Without one, answer MCP with no tools, so the connector stays quiet
    instead of showing as failed in every assistant that never automates anything."""
    entry = read_server_record()
    if entry_exists(entry) and bc_owned(entry):
        assert entry is not None
        env = dict(os.environ)
        env.update({str(name): str(value) for name, value in (entry.get("env") or {}).items()})
        command = str(entry["command"])
        os.execve(command, [command, *[str(arg) for arg in entry.get("args") or []]], env)
    return serve_stub(sys.stdin, sys.stdout)


def serve_stub(reader: Any, writer: Any) -> int:
    """A stdio MCP server with no tools: newline-delimited JSON-RPC 2.0, answering initialize, tools/list, and ping."""
    for line in reader:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if not isinstance(message, dict) or "method" not in message or "id" not in message:
            continue  # a notification, or an answer to a request this server never sends
        method = message["method"]
        answer: dict[str, Any] = {"jsonrpc": "2.0", "id": message["id"]}
        if method == "initialize":
            params = message.get("params") or {}
            answer["result"] = {"protocolVersion": params.get("protocolVersion") or "2025-06-18",
                                "capabilities": {"tools": {"listChanged": False}},
                                "serverInfo": {"name": "appium", "version": plugin_version()},
                                "instructions": STUB_INSTRUCTIONS}
        elif method == "tools/list":
            answer["result"] = {"tools": []}
        elif method == "ping":
            answer["result"] = {}
        else:
            answer["error"] = {"code": -32601, "message": f"{method} isn't available: {STUB_INSTRUCTIONS}"}
        writer.write(json.dumps(answer) + "\n")
        writer.flush()
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Buddy-Council Plugin Tools: detect, status, ensure, smoke, start, stop, prune, project, remove, serve.")
    sub = parser.add_subparsers(dest="action", required=True)
    for name, text in (("detect", "read the test project and this Mac"), ("status", "installed, recorded, and pending"),
                       ("smoke", "read one page source from the phone; installs nothing"), ("start", "start the project's own Appium server"),
                       ("remove", "delete bc's tools")):
        sub.add_parser(name, help=text).add_argument("--project", required=True, help="absolute path of the test project")
    ensure = sub.add_parser("ensure", help="install or upgrade, prove the server answers, record how it starts, prune")
    ensure.add_argument("--project", required=True)
    ensure.add_argument("--shared", default="", help="the Mac's missing shared tools to install side by side: jdk,android-sdk")
    project = sub.add_parser("project", help="run the project's own setup script")
    project.add_argument("--project", required=True)
    project.add_argument("--mode", required=True, choices=("check", "fix"))
    sub.add_parser("stop", help="stop the Appium server that start started")
    sub.add_parser("prune", help="remove pins and leftovers nothing points at")
    sub.add_parser("serve", help="the plugin's appium connector: appium-mcp as ensure recorded it, or no tools yet")
    args = parser.parse_args(argv)
    if args.action == "serve":
        return cmd_serve()
    try:
        if args.action == "detect":
            result = cmd_detect(args.project)
        elif args.action == "status":
            result = cmd_status(args.project)
        elif args.action == "ensure":
            result = cmd_ensure(args.project, args.shared)
        elif args.action == "smoke":
            result = cmd_ensure(args.project, "", install=False, phone_check=True)
        elif args.action == "project":
            result = cmd_project(args.project, args.mode)
        elif args.action == "start":
            result = cmd_start(args.project)
        elif args.action == "stop":
            result = {"stopped": stop_server()}
        elif args.action == "prune":
            result = cmd_prune()
        else:
            result = cmd_remove(args.project)
    except ToolError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
