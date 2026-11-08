# Jira router — the plugin's `atlassian` connector

[mcp-atlassian](https://github.com/sooperset/mcp-atlassian) talks to one Jira site per process: the one its
`JIRA_URL` names. This server is what the plugin registers as `atlassian`: it serves mcp-atlassian's own `jira_*`
tools, each with an optional `site`, and hands every call to a copy of mcp-atlassian for that site. It is how two
projects on two Jira sites both work, side by side, while each token only ever reaches its own site (ADR 0008).

```
assistant ──▶ atlassian (this router) ──┬─▶ mcp-atlassian copy for acme.atlassian.net   (that site's key)
                                         └─▶ mcp-atlassian copy for jira.acme.com/jira   (that site's key)
```

**It never talks to Jira itself.** It has no HTTP client, and its tests fail if it imports one: every request
to Jira comes from an mcp-atlassian copy.

## Tools

The 21 `jira_*` tools the plugin has always enabled, from `mcp-atlassian@0.23.1` — the same names and schemas, so
everyday Jira use keeps working, plus one optional argument:

- **`site`** — the Jira site: its base URL or host, e.g. `https://acme.atlassian.net`. Commands pass the
  project's `jira.base_url`. Left out, the call uses this computer's only Jira key, and fails, naming them, when
  there are several.

The schemas come from `tools.json`, a committed snapshot of mcp-atlassian's own, so the tools are listed before
any key exists and the assistant never waits for a download to see them. Each tool keeps mcp-atlassian's
annotations (`readOnlyHint` on the reads). Answers come back once, as text: mcp-atlassian's structured copy of
the same text is dropped, so a result never counts twice against an assistant's output limit.

## How a call is routed

1. The key for the call's `site` is read from the plugin's key store (`scripts/bc_keys.py`) — on **every** call,
   so a key saved while the assistant runs works on the next call.
2. The site's copy of mcp-atlassian is started on its first call — `uv tool run mcp-atlassian@0.23.1`, the `uv`
   the launcher found — and kept for the calls after it. A replaced key restarts that site's copy only; a copy
   that stops, even between calls, is started again on the next call, and a call still waiting on a copy that
   stops or is replaced gets an error instead of hanging — for a change, one that says to check whether it went
   through before calling again. A first start downloads mcp-atlassian, which can take half a
   minute: a call that would outwait that answers "still starting — call again", and the start carries on.
3. The copy gets its site's key in an `--env-file` that exists only while it starts — mode 600, in a private
   temporary folder, deleted once the copy answers — so the token is never on a command line or in a process's
   environment. A router stopped mid-start by SIGTERM or SIGHUP removes the folder before it goes, and one that
   was killed outright has its folder swept by the next router to start. Passing `--env-file` also stops
   mcp-atlassian from reading a stray `.env`. The copy's environment
   has every `JIRA_*`, `CONFLUENCE_*`, `ATLASSIAN_*`, `MCP_*`, and `FASTMCP_*` setting removed, so nothing from
   another site — or a setting such as `MCP_LOGGING_STDOUT` that would corrupt the stream — reaches it.

A key is `{"provider": "jira", "url", "username", "api_token"}` for Cloud (email + API token) or
`{"provider": "jira", "url", "personal_token"}` for Server/Data Center, plus `"ssl_verify": false` for a
self-signed certificate. The router sets mcp-atlassian's `TOOLSETS` and `ENABLED_TOOLS` itself.

## Running

The plugin starts it: every assistant runs `scripts/bc-mcp atlassian`, which finds `uv` and runs
`uv run --frozen python server.py` here. To refresh the snapshot after a pin change:

```bash
uv run --directory mcp-servers/jira-router python server.py --snapshot
```

It starts mcp-atlassian for a made-up site — it never connects to Jira — and rewrites `tools.json`.

## Tests

```bash
uv run --directory mcp-servers/jira-router python -m unittest discover -s tests -v
```

A stand-in (`tests/fake_atlassian.py`) takes mcp-atlassian's place and reports the site, key, and environment
each copy received, so the tests prove two sites go to two copies, each with only its own key.
