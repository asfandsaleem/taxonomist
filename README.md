# taxonomist

Generic taxonomy engine for document corpora. Decisions-first classification
(structured choice answers, calibrated probabilities) with a generative
fallback for vocabulary-gap suggestions. The taxonomy is a **data file** your
project versions; the store is a **protocol** you implement (or use the
bundled SQLite store).

## Jobs

- **tag** — fill missing tags at any level; `none` is a real answer
- **audit** — proactive verdict sampling (ok / wrong / weak / data_quality)
- **agent** — bounded curator session: model-driven tool loop for judgment
  calls (calibrated tag checks, canonical-only writes, node proposals)
- **evolve** — no-fit residue → clusters → node proposals → apply → tree grows
- **coverage / tree / observations / proposals / restore** — reporting + rollback

## Quickstart

```bash
pip install -e .
export OPENROUTER_API_KEY=...

# data: JSONL of {"id","title","text","meta":{...}}
taxonomist ingest docs.jsonl --db taxonomy.db
taxonomist coverage
taxonomist tag --level l3 --batch 1000          # decisions-first sweep
taxonomist observations --propose               # residue → node proposals
taxonomist proposals list; proposals accept 3; proposals apply 3
taxonomist audit -n 200                         # verdict sample
 taxonomist agent --turns 30                        # curator session
```

## The two brains

| Job | Engine |
|---|---|
| picks, verdicts, checks | Decisions API (`TAXONOMIST_DECISION_MODEL`, default `openai/gpt-6-luna-decisions`; struct fallback `typesafe/jev-1.13`) |
| suggestions, naming | Generative (`TAXONOMIST_GEN_MODEL`, default `z-ai/glm-5.3-flash`, Wafer→Together→Morph) |

## Bring your own store

The internal model is storage-agnostic — `Doc`, `Tag`, `Observation`,
`Proposal`. Everything persistence-shaped is an **adapter**:

```python
from taxonomist import Store, Doc, TagWrite
from taxonomist.adapters import MemoryStore, SqliteStore

class PgStore(Store):            # your backend: implement ~8 methods
    def docs(self, *, missing_level=None, limit=None): ...
    def tags(self, doc_id): ...
    def write_tags(self, items, provenance): ...  # snapshot internally
    def restore(self, snapshot_id): ...
    def observe(self, obs): ...
    def observations(self): ...
    def add_proposal(self, p): ...
    def proposals(self, status="pending"): ...
    def set_proposal(self, pid, status): ...
```

Bundled adapters: `SqliteStore` (default, `taxonomy.db` in your project
dir) and `MemoryStore` (tests/dev — also the minimal adapter example).
No postgres dependency anywhere in the package.

Every write is snapshotted; `taxonomist restore <snapshot_id>` rolls back.
