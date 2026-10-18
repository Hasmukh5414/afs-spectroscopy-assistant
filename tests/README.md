# Software checks

```sh
uv sync --locked --extra ingestion --group dev --group audit
AFS_TEST_LIVE=0 AFS_TEST_PDF= uv run --locked --extra ingestion --group dev pytest -q
uv run --locked --extra ingestion --group dev python -m pyright
node --check src/afs_assistant/web/app.js
```

The default suite uses fixture providers, temporary databases and a local SpaCy
sentencizer. It does not call real models or load organizer answers as expected
technical results. The ingestion extra is needed for the complete unit suite.

Optional lanes:

- `AFS_TEST_LIVE=1 uv run --locked --group dev pytest tests/connectivity -q`
  makes real neutral embedding/JSON-provider requests with your own configuration.
- `AFS_TEST_PDF=/absolute/path/source.pdf uv run --locked --extra ingestion --group dev --group audit pytest tests/integration/test_docling.py -q`
  runs actual Docling extraction; first use can download parser/OCR models.

Tests cover retrieval/rank fusion, chunk boundaries, image enrichment/cache reuse,
source hashes/ownership, portable index integrity, quote/citation/quantity checks,
repair/omission, agent tools/checkpoints, reports and configuration relocation.
Passing them does not establish scientific claim support, safe hardware ratings
or a good experiment. Final scenario assessments live in [results](../results).
