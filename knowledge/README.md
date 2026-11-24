# `knowledge/` — Curated Cleanroom Chrysler Heritage & Hybrid RAG

`knowledge/` stores cleanroom, **3-tier provenance Markdown digests** covering 1984–1995 Turbo Chrysler LM/PM, SMEC (`SMEC_88_89`), and SBEC (`SBEC_90_93`) hardware, 62.5k SCI diagnostic tables, injector characterizations, and verified Mermaid wiring schematics.

## 3-Tier Provenance Model
| Tier | Weight | Description | Example Sources |
| :--- | :---: | :--- | :--- |
| **Tier 1** | `1.25x` | Verified primary technical specs, FSMs, & bench measurements | Factory Service Manuals, verified SMEC/SBEC board traces, SCI frame captures |
| **Tier 2** | `1.10x` | Reputable community disassemblies & cross-checked references | Well-established TurboDodge / Shelby SMEC/SBEC disassembly notes |
| **Tier 3** | `0.90x` | Anecdotal forum tips & unverified hypotheses (flagged explicitly) | Unverified forum claims (never used as sole basis for pins or hex offsets) |

## 4-Stage Heritage & Verified Schematic Pipeline
1. **Web Preservation (`crawl_warc.py` + `parse_web.py`)**: Captures archival sources at `1 req/s` via `warcio` (`.warc.gz` in gitignored `data/heritage_raw/`), normalizes `<pre>`/`<table>` blocks with `lxml` into GFM pipe tables, extracts prose via `Trafilatura`, and stitches multi-page threads with `Crawl4AI`.
2. **Document OCR & Mermaid Gate (`parse_docs.py` + `mermaid_gate.py`)**: Extracts tables with `Docling` `TableFormer`, crops figures at `300 DPI`, stitches cross-page tables, and runs `Marker`+`Surya` OCR fallback for margin annotations. Transpiled Mermaid diagrams pass Gate 4a (`mmdlint` AST `<= 2` retries) and Gate 4b (Page-OCR token grounding) to emit `VERIFIED` or quarantine as `NEEDS_HUMAN_REVIEW` alongside 3–5 bullet walkthroughs.
3. **Parent-Child Scope Chunking (`chunker.py`)**: Prepends a 2-line `[DOC|TIER|ECU|ENGINE|SECTION]` scope header to every chunk, splits `> 35`-row tables into `15–20` row slices repeating header/separator rows, and pairs child retrieval chunks with parent sections.
4. **Hybrid Index & Trust-Weighted RRF (`hybrid_index.py` + `search.py`)**: Indexes `heritage_parents`, `heritage_children`, `heritage_fts` (`tokenize = "unicode61 remove_diacritics 0 tokenchars '$_-#.'"`), and `heritage_vec` (`BAAI/bge-m3` `1024-d` cosine embeddings) in rebuildable `data/heritage_index.sqlite`. Fuses results via Trust-Weighted RRF (`k = 60`), optional `BAAI/bge-reranker-v2-m3`, `parent_id` hydration, and strict **`No Verified Source Match`** abstention.

## Required YAML Frontmatter Fields
In addition to provenance metadata (`doc_id`, `trust_tier`, `title`, `source_type`, `source_citation`, `warc_capture_date`, `page_ref`, `ecu_families`, `hex_symbols`), digests declare scope and verification fields:
- `ecu_scope`: e.g., `SMEC_88_89`, `SBEC_90_93`, `LM_PM_84_87`
- `engine_scope`: e.g., `2.2L_K`, `2.5L_K`, `2.4L_EDZ`, `2.0L_ECC`
- `symbols_referenced`: verified ROM/RAM/part symbols (`$8000`, `0xF2`, `PEFTBL`, `P4529353`)
- `pins_referenced`: verified 60-way / diagnostic pins (`Pin 11`, `Pin 2`, `Pin 3`, `Pin 25`, `Pin 45`)
- `mermaid_verification`: `VERIFIED` | `NEEDS_HUMAN_REVIEW` | `NONE`

## Interfaces
- **CLI**: `uv run logicaide heritage crawl`, `heritage parse-web`, `heritage parse-docs`, `heritage build-index` (alias `heritage index`), `heritage query`, `heritage diagram`
- **FastMCP (`chrysler-heritage`)**: `search_heritage_docs`, `get_verified_schematic`, `lookup_60way_pin`, `lookup_sci_offset`, `get_provenance_card`
