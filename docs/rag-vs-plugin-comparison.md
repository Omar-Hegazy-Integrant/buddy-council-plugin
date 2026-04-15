# RAG Approach vs Plugin Approach: Detailed Comparison

## Architecture Overview

| Aspect | RAG (Buddy-Council v1) | Plugin (Buddy-Council v2) |
|--------|----------------------|--------------------------|
| **Runtime** | FastAPI web server + custom UI | Claude Code / Copilot CLI (terminal) |
| **LLM** | Local Ollama (Qwen3:14b, Llama3.1:8b) | Claude (Sonnet/Opus via API) |
| **Data retrieval** | Qdrant vector search + embeddings | Live MCP fetch from APIs |
| **Preprocessing** | Offline pipeline → embed in Qdrant | On-the-fly normalization skill |
| **Agents** | 7 agents (Python + LangGraph) | 2 agents (markdown instructions) |
| **Orchestration** | OrchestratorAgent (regex + LLM classifier) | `/bc:ask` intent routing (Claude reasoning) |

---

## Data Freshness

| | RAG | Plugin |
|--|-----|--------|
| **Pros** | Fast retrieval after indexing — sub-second lookups | Always current — fetches live from TestRail/Excel every time |
| **Cons** | Data goes stale the moment you index. If someone updates a requirement in Jama or adds a test case in TestRail, the system doesn't know until you re-run the preprocessing pipeline | Every request hits the API — slower for large datasets, susceptible to rate limits |
| **Verdict** | Good for stable, infrequently-changing data | Better for actively evolving requirements/test suites |

---

## Retrieval Quality

| | RAG | Plugin |
|--|-----|--------|
| **Pros** | Two-phase retrieval with cross-encoder reranking finds semantically related documents even without explicit links. Can surface "CWA-REQ-92 might be related to CWA-REQ-15" based on embedding similarity | Gets the exact data it needs — no retrieval noise, no missed context due to embedding quality |
| **Cons** | Embedding quality is the ceiling. `all-mpnet-base-v2` (768d) may miss domain-specific relationships. Noisy or poorly-worded requirements produce poor embeddings | No semantic discovery — can only find relationships through explicit `linked_ids`. Won't notice "these two unlinked requirements contradict each other" unless they're in the same feature |
| **Verdict** | Better for discovering hidden relationships | Better for analyzing known, explicitly-linked artifacts |

---

## Analysis Quality

| | RAG | Plugin |
|--|-----|--------|
| **Pros** | Retrieval focuses context — the LLM sees only the most relevant documents, reducing noise | Claude (Sonnet/Opus) is significantly more capable than Qwen3:14b for nuanced reasoning. Larger context window handles more artifacts at once |
| **Cons** | Qwen3:14b is limited — weaker at complex multi-step reasoning, smaller context window, more prone to hallucination | Without retrieval, the agent may need to process more data to find the relevant pieces. Batching by feature helps but isn't as targeted as vector search |
| **Verdict** | RAG's retrieval helps a weaker model focus. But the model itself limits ceiling. | Stronger model compensates for less targeted data selection. Higher quality ceiling. |

---

## Infrastructure & Operations

| | RAG | Plugin |
|--|-----|--------|
| **Pros** | Runs entirely locally — no API costs, no network dependency (except TestRail), works offline once indexed | Zero infrastructure — no server, no database, no GPU. Plugin ships as markdown files + one small MCP server |
| **Cons** | Requires: Ollama + GPU, Qdrant instance, Python environment, FastAPI server. Preprocessing pipeline must be maintained and re-run. Cross-encoder reranker needs a separate model loaded | Depends on Claude API (cost per request). Depends on network connectivity. MCP server needs `uv`/Python |
| **Verdict** | More operational burden but zero per-request cost | Near-zero ops but pay-per-use |

---

## Cost

| | RAG | Plugin |
|--|-----|--------|
| **Pros** | After hardware investment, marginal cost per query is effectively zero. Run thousands of analyses without billing | No upfront hardware. No GPU needed. No database hosting |
| **Cons** | Needs a machine with GPU for Ollama (or CPU with significant slowdown). Qdrant needs RAM for vector storage. Maintenance time for pipeline | Every analysis calls Claude API — costs ~$0.01-0.10 per scoped analysis, more for "all" scope. At high volume, costs add up |
| **Verdict** | Better for high-volume, repeated analysis | Better for occasional/on-demand analysis |

---

## Developer Experience

| | RAG | Plugin |
|--|-----|--------|
| **Pros** | Full control over every component. Can debug retrieval, inspect embeddings, tune reranker thresholds | `/bc:contradiction` just works in the terminal. No setup beyond config. Agents are readable markdown — anyone can understand and modify the logic. Works on both Claude Code and Copilot CLI |
| **Cons** | Custom web UI to maintain. 7 agents across Python files with LangGraph state machines. ~2000+ lines of agent code. New team members need to understand LangGraph, Qdrant, cross-encoder reranking | Less control over LLM behavior. Can't inspect "why did it miss this contradiction?" the way you can inspect retrieval scores. Markdown agents are powerful but opaque when they misbehave |
| **Verdict** | More debuggable but steeper learning curve | Dramatically simpler to build, share, and modify |

---

## Extensibility

| | RAG | Plugin |
|--|-----|--------|
| **Pros** | Python codebase — can add any library, any integration, any custom logic | Adding a new provider = one markdown file. Adding a new agent = one markdown file. Provider abstraction means swapping Jama for Jira is a config change. Works on Claude Code AND Copilot CLI with no code changes |
| **Cons** | Adding a new data source means: new processor, new embedding logic, new Qdrant collection/filters, new agent retrieval logic. Tightly coupled to the embedding pipeline | Constrained by what the LLM can do in a single conversation. Can't add custom retrieval algorithms or hybrid search strategies |
| **Verdict** | More flexible at the cost of more work per extension | Faster to extend but within the boundaries of the agentic framework |

---

## Accuracy & Coverage

| | RAG | Plugin |
|--|-----|--------|
| **Semantic discovery** | Better — embeddings find non-obvious relationships | Worse — limited to explicit links and same-feature grouping |
| **Precision** | Lower — retrieval may include irrelevant documents | Higher — works with exact data, less noise |
| **Complex reasoning** | Weaker model limits depth | Claude catches subtler contradictions |
| **Cross-feature analysis** | Embedding similarity works across features | Requires batching + summary propagation |

---

## When RAG Wins

1. **Large, stable datasets** — requirements/test cases change infrequently, so stale data isn't a concern
2. **High query volume** — hundreds of analyses per day where API costs matter
3. **Semantic discovery is critical** — you need to find "hidden" relationships between unlinked artifacts
4. **Offline/air-gapped environments** — can't call external APIs
5. **Custom retrieval tuning** — you want to control exactly how documents are ranked and filtered

## When Plugin Wins

1. **Actively evolving data** — requirements and test cases change frequently
2. **Team adoption** — anyone with Claude Code or Copilot CLI can use it immediately, no infrastructure setup
3. **Analysis quality matters most** — Claude's reasoning is significantly stronger than local models
4. **Multi-platform** — same plugin works on Claude Code and Copilot CLI
5. **Maintenance budget is low** — markdown agents vs thousands of lines of Python + LangGraph
6. **Adding new data sources** — one file per provider vs a full pipeline

---

## Hybrid Possibility

The approaches aren't mutually exclusive. A future architecture could:

1. **Use the plugin for orchestration + analysis** (Claude's strength)
2. **Use a lightweight embedding index for semantic discovery** (RAG's strength)
3. The plugin's MCP server could expose a `testrail_find_similar_cases` tool backed by a local embedding index
4. This would give you live data + semantic discovery + strong reasoning — best of both worlds
