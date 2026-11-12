# The Walk Plan is committed, and its walks are planned by code, refined by an agent, and checked by code

Before the plugin can walk an app to build its Screen Map, it needs to know which screens to visit and how to reach them. `/bc:gather-locators` works that out from Test Cases: it reads one or more TestRail folders and writes a Walk Plan — every Screen State the cases mention, the components and labels on each screen, the Conditions each state needs, and the fewest Walks that visit every Screen State on each Platform. The Walk Plan is committed to the Test Project, in a `walk-plan/` folder inside the Screen Map's folder, and goes through the same reviewed pull request as the Screen Map. This changes a consequence of [The Screen Map is shared in git](./0004-screen-map-in-git-pull-request-offered.md): the Screen Map is no longer the only thing the plugin shares. The Walk Plan is shared because it is reviewable intent — a teammate can check it against the cases it cites — and because regenerating it costs a run over every case. It stays apart from the Screen Map: the Walk Plan is what the Test Cases claim, and the Screen Map is what walking the phone proved, so nothing guessed from a case reaches the Screen Map.

Choosing the fewest Walks is mostly mechanical, so code does it. `scripts/bc_walk.py` plans Walks that need no Conditions and Walks that do, each set minimal on its own: fewest Walks, then fewest Conditions to prepare, then fewest steps, with a soft cap of about 40 steps a Walk. An agent then merges and splices them into fewer Walks, and names every pair of Conditions it judged able to hold on one phone at once — the one judgment code can't make. Code replays every Walk it returns against the graph: each step starts where the last ended, every transition and component exists on that Platform, every Condition is prepared or set before it's needed and not cleared since, every Screen State the separate Walks reached is still reached, and the step cap holds. A Walk that fails goes back to the agent once; a second failure keeps the separate Walks, and the run reports the failures with one suggested change to the agent's instructions.

## Considered options

- **An agent plans every Walk from the graph, and code checks them.** The documented alternative. It may merge better than splicing the separate Walks, but a failure has no smaller plan to fall back to.
- **Code only: the separate Walks, never merged.** No agent, and always correct, at the cost of more Walks. It is what a run falls back to.
- **Keep the Walk Plan on each Mac, like Captures.** Every teammate would pay for the same run over every case, and no one would review what the walk is about to do.
- **Put the Walk Plan into the Screen Map.** Guessed elements would sit beside proven ones, and a reader couldn't tell them apart.

## Consequences

- Every node carries a stable ID, the Test Cases it came from, and the Platforms it appears on, and the files are written sorted, so a diff shows only what changed. How a regenerated plan keeps the committed IDs is left for later.
- Walks pass the agent's compatible Condition pairs to `walks.md`, so the one thing only a model judged is visible in review.
- The agent never edits its own instructions. A suggested change lands in a plugin release, after a person reads it, so one team's app never shapes a public plugin.
- No Screen State is dropped: one that needs a person — turning off the network, say — gets a step that names what to do, and at worst a Walk of its own.
- Fetching and extracting run on low-cost agents, and code checks each result before anything builds on it: the fetched cases against `sources.json`, every extracted fragment against its schema.
