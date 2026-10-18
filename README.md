# AFS Assistant

A local, source-grounded spectroscopy assistant built for the AFS hackathon.

Describe a customer's measurement problem or ask a technical question. The
assistant searches the supplied AFS material (application papers, process
control slides, flyer, method-comparison workbook and laboratory web page),
lets an LLM agent inspect the evidence, and returns a structured answer in
which every claim links to the exact passage, table row, figure or PDF page it
came from.

For a customer use case it produces an **assessment**: the customer's
requirements, candidate measurement methods with cited claims and limitations,
open questions, risks, and a first feasibility study. Answers are checked in
code for quotes, citations, quantities and method consistency before they are
shown.

How it works in detail: **[docs/architecture.md](docs/architecture.md)**.

---

## Quick start

You need **Python 3.11+**, **[uv](https://docs.astral.sh/uv/getting-started/installation/)**,
and access to three model services (any OpenAI-compatible server such as
Ollama or vLLM, or Claude for text/vision):

| Service | Model used by the shipped index |
| --- | --- |
| Text | `qwen3.6:35b-a3b` (any capable chat model with JSON-schema output and tool calling) |
| Embeddings | `qwen3-embedding:0.6b`, 1024 dimensions (**must** match the index) |
| Vision (optional at runtime) | `gemma4:31b-it-qat` |

```sh
git clone https://github.com/Hasmukh5414/afs-spectroscopy-assistant.git
cd afs-spectroscopy-assistant

# 1. Install dependencies
uv sync --locked --no-dev

# 2. Configure
cp .env.example .env          # then edit URLs, keys and model names

# 3. Install the prepared index (no parsing or embedding needed)
AFS_MODE=demo uv run --locked --no-dev afs-assistant install-index data/index/afs-official-v2.zip

# 4. Check that the configured services respond
uv run --locked --no-dev afs-assistant check-services

# 5. Start
uv run --locked --no-dev afs-assistant serve
```

Open **http://127.0.0.1:8000/**.

Step 3 copies the database seed to `var/afs.sqlite3` and the source files,
page images and figure crops to `data/`. The `AFS_MODE=demo` prefix only makes
that step run without contacting any model; your `.env` stays in real mode.

---

## Configuration

All settings live in `.env` (template: [.env.example](.env.example), every
variable documented with its default and allowed range). Shell variables
override the file.

The essentials:

| Variable | Meaning |
| --- | --- |
| `AFS_MODE` | `openai` = real services, `demo` = no model calls |
| `AFS_PROVIDER` | `openai` (OpenAI-compatible) or `anthropic` (Claude) |
| `AFS_API_BASE`, `AFS_API_KEY`, `AFS_MODEL` | Text model |
| `AFS_EMBEDDING_API_BASE`, `AFS_EMBEDDING_API_KEY`, `AFS_EMBEDDING_MODEL`, `AFS_EMBEDDING_DIMENSION` | Embeddings; empty URL/key inherit the text service |
| `AFS_VISION_API_BASE`, `AFS_VISION_API_KEY`, `AFS_VISION_MODEL` | Vision; empty URL/key inherit the text service |
| `AFS_DATABASE`, `AFS_DATA_DIR` | Where the database and data assets live |

**Using Claude:** set `AFS_PROVIDER=anthropic`, `AFS_API_KEY` to your Anthropic
key and `AFS_MODEL`/`AFS_VISION_MODEL` to Claude model IDs. Claude has no
embedding endpoint, so `AFS_EMBEDDING_API_BASE` and `AFS_EMBEDDING_API_KEY`
must point to an OpenAI-compatible server running `qwen3-embedding:0.6b`.

---

## Using the assistant

* **New chat** — type a question or paste a customer scenario. A live activity
  panel shows routing, searches, reads and checks while the answer is written
  (typically 1–3 minutes on a local model).
* **Follow-ups** — continue in the same chat; corrections replace earlier facts.
* **Sources** — click any citation to see the chunk, workbook row as a table,
  figure crop, rendered PDF page, or open the original file at that page.
* **Export** — download an assessment as Markdown or JSON.
* **Retry** — a failed latest answer can be retried from the same evidence.

---

## Databases and indexes

The assistant reads one *evidence release* from SQLite:

* **Semantic index** — the passage embeddings, stored with the corpus in the
  `releases` table of `var/afs.sqlite3`.
* **Lexical index** — BM25, rebuilt in memory from the same corpus at load time.
* **Assets** — originals, page images and figure crops under `data/`.

Chats and agent checkpoints are stored in `var/afs.sqlite3` and
`var/agent-checkpoints.sqlite3`.

### Option A — install the shipped index (recommended)

```sh
AFS_MODE=demo uv run --locked --no-dev afs-assistant install-index data/index/afs-official-v2.zip
```

`data/index/afs-official-v2.zip` contains the clean database seed (392 chunks,
392 vectors, 57 image descriptions, no chats), the seven source originals, 64
page images and the figure crops. Every file is checksum-verified before
anything is written. The installer refuses to overwrite an existing database:
to reinstall, move `var/afs.sqlite3` away or point `AFS_DATABASE` to a new path.

Verify the archive itself with:

```sh
cd data/index && shasum -a 256 -c SHA256SUMS
```

### Option B — rebuild the index from the supplied materials

Needed when you change the documents, chunking, embedding model or generated
image descriptions. Changing answer prompts or the text model does not require
rebuilding document embeddings. Requires
the ingestion extra; the first run downloads ~550 MB of Docling/OCR models into
`data/models/`.

```sh
uv sync --locked --extra ingestion
uv run --locked --extra ingestion afs-assistant prepare-official \
  --release afs-official-v3 --describe-images --embed --publish
```

This runs admission → Docling extraction → chunking → vision descriptions →
embeddings → validation → publication. Each stage is cached under
`data/processed/` and `data/cache/`, so re-runs only redo what changed; with
the existing caches, unchanged documents need no new Docling, vision or
embedding work.
Release IDs are immutable; use a new ID for changed content. A failed run
publishes nothing and keeps the previous release active.

### Adding or replacing documents

The official pipeline admits a fixed list of files:

1. Put the file in `data/materials/` and add it to `OFFICIAL_FILES` in
   [src/afs_assistant/indexing/official.py](src/afs_assistant/indexing/official.py)
   (source ID, filename, title, role `technical`/`research`/`business`,
   visual strategy `regions` for papers or `page` for slide decks, DOI keys).
2. Update the coverage expectations in `OfficialExtractor.validate_corpus`
   (page count, reference counts) so validation passes.
3. Run `prepare-official` with a **new** release ID (Option B).

### Using the included index on another machine

The repository already includes `data/index/afs-official-v2.zip`; no export or
re-embedding is needed. After installing dependencies and copying `.env.example`
to `.env`, run this from the repository root:

```sh
AFS_MODE=demo uv run --locked --no-dev afs-assistant install-index data/index/afs-official-v2.zip
```

This restores the clean database, document embeddings and matching source assets.
The installer refuses to overwrite an existing database. Configure your own model
services in `.env` before starting the assistant; query embeddings must use the
same embedding model as the included index.

### Inspecting retrieval

```sh
uv run --locked --no-dev afs-assistant search "inline moisture in polymer melt"               # BM25 only
uv run --locked --no-dev afs-assistant search "inline moisture in polymer melt" --mode hybrid # BM25 + embeddings
```

---

## Commands

| Command | Purpose |
| --- | --- |
| `afs-assistant serve [--port 8000]` | Start UI + API on 127.0.0.1 |
| `afs-assistant check-services` | Test the embedding and JSON-answer services |
| `afs-assistant install-index <zip>` | Install a portable index |
| `afs-assistant export-index <zip>` | Export the active index |
| `afs-assistant prepare-official --release <id> [--describe-images] [--embed] [--publish]` | Build an index from `data/materials` |
| `afs-assistant search "<query>" [--mode hybrid] [--limit N]` | Query retrieval directly |
| `afs-assistant demo-data` | Load synthetic evidence (with `AFS_MODE=demo`) |

Prefix with `uv run --locked --no-dev` (or `--extra ingestion` for preparation).

---

## Evaluation scenarios

Final assessments for the six evaluation scenarios H01–H06 are in
[results/](results).

To regenerate them through the same API the browser uses:

```sh
uv run --locked --group dev python scripts/run_scenarios.py --set final        # H01–H06
uv run --locked --group dev python scripts/run_scenarios.py --set development  # D01–D08
uv run --locked --group dev python scripts/run_scenarios.py --set final --only H03
```

Runs use an isolated temporary database and write Markdown + JSON to
`var/scenario-runs/<timestamp>/`.

---

## Tests

```sh
uv sync --locked --extra ingestion --group dev --group audit
uv run --locked --extra ingestion --group dev pytest -q
uv run --locked --extra ingestion --group dev python -m pyright
```

The default suite is offline (fixture providers, temporary databases). Opt-in
lanes are described in [tests/README.md](tests/README.md).

---

## Repository layout

| Path | Contents |
| --- | --- |
| `src/afs_assistant/` | Application: API, web UI, retrieval, agent, validation, indexing |
| `docs/architecture.md` | Full architecture and data flow |
| `data/materials/` | Supplied source documents, organizer inputs, challenge brief (+ `SHA256SUMS`) |
| `data/index/` | Portable prepared index `afs-official-v2.zip` (+ `SHA256SUMS`) |
| `data/originals/`, `data/processed/`, `data/cache/` | Local prepared assets (not in Git) |
| `var/` | Local databases: chats, index, agent checkpoints (not in Git) |
| `results/` | Final H01–H06 assessments |
| `scripts/run_scenarios.py` | Scenario runner |
| `tests/` | Unit, integration and opt-in connectivity tests |

---

## Limits

This is an expert-review prototype, not a certified recommendation system.
Code checks confirm citations, quotes, quantities and method/geometry
consistency, but not that a source scientifically entails each claim. Image
descriptions are AI-generated and unreviewed. The server is meant for local
use on loopback.
