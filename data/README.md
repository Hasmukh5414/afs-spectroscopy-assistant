# data/

| Folder | In Git | Contents |
| --- | --- | --- |
| `materials/` | yes | Supplied files: S01–S05 PDFs, `AFS_method_comparison_table.xlsx`, `S06_Laboratory_Link.txt`, organizer inputs (`AFS_Examples_and_Reference.docx`, `customer_scenarios_for_final_evaluation.txt`), `challenge-description.pdf`, `SHA256SUMS` |
| `index/` | yes | `afs-official-v2.zip` — portable prepared index (database seed with 392 chunks and embeddings, originals, page images, figure crops) and `SHA256SUMS` |
| `originals/` | no | Hash-addressed copies of the admitted sources + `official-materials.json` receipt |
| `processed/` | no | Docling output per source: page images, figure crops, `docling.json`, `material.json` |
| `cache/` | no | `chunks/`, `image_descriptions/` (vision output), `vectors/` (embeddings) for re-use on rebuild |

`install-index` fills `originals/` and `processed/`; `prepare-official` fills
all three local folders. Preparation also creates `models/` (Docling/OCR
downloads), `runs/` (run reports), `releases/` (JSON copy of the corpus) and `guidance/`; these can be deleted.

See [docs/architecture.md](../docs/architecture.md) §4 and §8.
