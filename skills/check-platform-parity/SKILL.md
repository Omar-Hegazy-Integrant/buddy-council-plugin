---
description: Internal (used by /bc:vnv-sprint-prep) — Group sprint stories by platform using the Jira "OS" field, pair each story with its counterpart on the other platform, and report stories that are missing a counterpart or have one that is not linked.
user-invocable: false
---

# Check Platform Parity — Skill

Every user-facing story should exist on each platform the team ships. This skill takes a sprint's stories,
works out which platform each belongs to, pairs them up, and reports the ones that are missing a counterpart
or have a counterpart nobody linked.

It **reports only**. It never creates a counterpart story and never creates a link. `mcp-atlassian` *can*
create issue links (`jira_create_issue_link`), so this is a deliberate boundary rather than a limitation:
linking two stories the team never agreed are counterparts, or inventing a missing one, is a dev-team
decision. Report the gap and let a human close it.

## Input

- `stories`: the sprint's issues in canonical schema (`type: "board_issue"`), from `fetch-board-issues`.
- Config at `.buddy-council/sources.json` → `jira.platform`:

```json
{
  "field_name": "OS",
  "field_id": "customfield_10050",
  "values": {
    "ios": ["iOS", "IOS", "iPhone"],
    "android": ["Android"]
  },
  "require_parity": ["ios", "android"],
  "title_prefix_fallback": true
}
```

`require_parity` lists the platforms that must exist in pairs. A story on a platform outside that list (a
backend or web story) is reported `n/a`, never as a gap.

## Step 1: Resolve the OS field id

The `OS` field is a custom field, so its id differs per site. Resolve once per run, in this order:

1. **`jira.platform.field_id` in config** — use it directly. Validate it appears in at least one fetched
   issue's `fields`; if it never does, treat it as stale and fall through to discovery.
2. **`jira_search_fields`** with `keyword` set to `jira.platform.field_name` (default `"OS"`). Match the
   returned `name` case-insensitively and take its id. `jira_get_project_fields` for the dev project is the
   fallback when the keyword search is ambiguous — it lists only fields that project actually uses.
3. **The issue payload's `names` map**, when `jira_get_issue` returns one.

Cache the resolved id back into `jira.platform.field_id` so later runs skip discovery.

If the field cannot be resolved at all, say so plainly — *"No `OS` field found on project PROJ; falling back
to title prefixes, so parity results are lower-confidence"* — and continue with step 2's fallback. Do not
fail the run, and do not present prefix-derived results as if they came from the field.

## Step 2: Determine each story's platform

**Primary signal — the OS field.** Read `fields.<field_id>`. Jira custom fields come back in three shapes;
handle all of them:

| Shape | Example | Read |
|---|---|---|
| Plain string | `"iOS"` | the string |
| Single select | `{"value": "iOS", "id": "10101"}` | `.value` |
| Multi select | `[{"value": "iOS"}, {"value": "Android"}]` | every `.value` |

A multi-select story covering both platforms is **not** a parity gap — it is one story serving both. Mark it
`platform: "both"`, `parity: "n/a"`, and say why.

Match each raw value against `jira.platform.values` case-insensitively. An unrecognized value is reported
verbatim as `platform: "unknown (<value>)"` — never silently coerced into iOS or Android.

**Fallback — the title prefix.** Only when the OS field is absent or empty on that story, and
`title_prefix_fallback` is true. Match a leading `[iOS]`, `(Android)`, `iOS -`, `Android:` and similar
against the same `values` map. Record `platform_source: "title_prefix"` so the report can flag it.

Always record which signal was used per story. When the two disagree — the field says Android, the title
says `[iOS]` — **trust the field**, and list the disagreement as its own finding: a mislabelled title is
worth fixing and is often the symptom of a copy-pasted clone.

## Step 3: Pair the stories

For each story on a platform in `require_parity`, look for its counterpart on each *other* required platform,
in this order. Stop at the first that produces a match, and record how it matched:

1. **Issue link** — scan `fields.issuelinks` for a linked issue that is in the sprint set and resolves to the
   other platform. → `parity: "linked"`. This is the only state that needs no action.
2. **Shared parent / epic** — same `fields.parent.key`, other platform, and a similar summary once the
   platform prefix is stripped. → `parity: "exists-not-linked"`.
3. **Normalized summary** — strip any platform prefix and compare the remaining text. Require a strong match,
   not a loose one; when unsure, report no match rather than a wrong pairing. → `parity: "exists-not-linked"`.

No match on any of the three → `parity: "missing"`.

## Output

Return one record per story, and render the table the agent prints:

```json
[
  {
    "key": "PROJ-123",
    "summary": "Retry failed sync on the vitals dashboard",
    "platform": "ios",
    "platform_source": "os_field",
    "counterpart": "PROJ-124",
    "parity": "linked",
    "matched_by": "issue_link"
  },
  {
    "key": "PROJ-131",
    "summary": "Add offline banner",
    "platform": "ios",
    "platform_source": "title_prefix",
    "counterpart": null,
    "parity": "missing",
    "matched_by": null
  }
]
```

| `parity` | Meaning | What the report says |
|---|---|---|
| `linked` | Counterpart exists and is linked | Nothing to do |
| `exists-not-linked` | Counterpart exists, no Jira link | **Highlight.** Name both keys and how they were matched, so someone can link them in the UI |
| `missing` | No counterpart found | **Highlight.** The other platform may be genuinely out of scope this sprint — ask, don't assume |
| `n/a` | Platform outside `require_parity`, or one story covers both | Listed, not flagged |

Order the report so `missing` comes first, then `exists-not-linked`, then the rest. Sort each group by key so
successive runs produce a stable, diffable list.

Close with a one-line count and the confidence caveat when it applies:

```
Parity: 12 stories — 8 linked, 2 not linked, 1 missing counterpart, 1 n/a (backend)
        3 of 12 platforms came from title prefixes, not the OS field — lower confidence
```

## Error Handling

- **No stories in scope** → return an empty array; the caller reports "no active sprint stories".
- **OS field unresolvable** → fall back to prefixes, state it loudly, mark every affected row.
- **Unrecognized OS value** → report verbatim as `unknown (<value>)`; never guess a platform.
- **Ambiguous summary match** → prefer reporting `missing` over asserting a pairing you are not confident in.
  A false "missing" costs a question; a false pairing hides a real gap.
