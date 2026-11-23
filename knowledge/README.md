# `knowledge/` — Curated Cleanroom Chrysler Heritage & Hybrid RAG

`knowledge/` stores cleanroom, **3-tier provenance Markdown digests** covering 1988–1993 Turbo Chrysler SMEC/SBEC hardware, 62.5k SCI diagnostic tables, injector characterizations, and period tuning notes.

## 3-Tier Provenance Model
| Tier | Description | Example Sources |
| :--- | :--- | :--- |
| **Tier 1** | Verified primary technical specs, bench measurements, & cleanroom pinouts | Verified SMEC/SBEC board traces, SCI frame captures, datasheet limits |
| **Tier 2** | Reputable community disassemblies & cross-checked Mopar Turbo references | Well-established TurboDodge / Shelby SMEC/SBEC disassembly notes |
| **Tier 3** | Anecdotal forum tips & unverified hypotheses (flagged explicitly) | Unverified calibration claims requiring `m68hc11` or bench validation |

## Ingestion & Hybrid Search Pipeline
- **Cleanroom Separation**: Raw web scrapes (`Trafilatura`) and local PDF/doc parses (`Docling`) live exclusively in gitignored `data/heritage_raw/`. Only distilled, non-copyrighted Markdown digests are committed to `knowledge/`.
- **Hybrid Index**: Combines SQLite `FTS5` (exact lexical matching for hex addresses, Chrysler part numbers, and SCI PIDs) with `sqlite-vec` vector embeddings for semantic retrieval.
- **No Guessing Rule**: If a hex offset, RAM address, or pinout is absent from Tier 1/2 sources and `rom_manifest.json`, queries return `No Verified Source Match`.

## Interfaces
- CLI: `uv run logicaide heritage ...`
- FastMCP: `chrysler-heritage` (`mcp_servers/`)
