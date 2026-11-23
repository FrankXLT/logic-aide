# Chrysler ECU Heritage Preservation & Hybrid RAG Specification

Decades of primary 1984–1993 Chrysler LM/SMEC/SBEC documentation live on aging enthusiast domains (with expiring domains/TLS certificates) or inside physical engineer binders. LogicAIDE preserves these technical artifacts inside an offline, provenance-verified Retrieval-Augmented Generation (RAG) knowledge base that enforces strict copyright cleanroom boundaries and never guesses unverified hex addresses or pinouts (`AGENTS.md`).

---

## 1. Cleanroom Separation Invariant

| Storage Zone | Git Tracked? | Permitted Contents | Rules |
| :--- | :---: | :--- | :--- |
| **`data/heritage_raw/`** | **No** (`.gitignore`) | Raw copyrighted PDFs, scanned Chrysler engineer binders, raw HTML/WARC snapshots, Wayback CDX dumps. | Local offline vault only; never committed, never copied verbatim into `knowledge/`. |
| **`knowledge/`** | **Yes** | Attributed cleanroom fact sheets, distilled Markdown technical digests, YAML provenance headers, verified pinout/hex cross-references, and derived index manifests. | Every document MUST carry valid YAML provenance frontmatter and a `trust_tier` badge (`Tier 1`, `Tier 2`, or `Tier 3`). |
| **`data/heritage_index.sqlite`** | **No** (rebuildable) | Compiled `FTS5` inverted lexical index + `sqlite-vec` (`vec0`) dense embeddings generated from `knowledge/`. | Reproducible deterministically from `knowledge/**/*.md` via `uv run logicaide heritage index`. |

---

## 2. Dual Ingestion Stack (`Trafilatura` + `Docling`)

### 2.1 Web & Archive Preservation (`Trafilatura` + Wayback/WARC)
Targets vanishing **Turbo-Mopar**, **Turbo Dodge**, **SDAC**, **MiniMopar**, and **Turbonator** technical pages:
1. Capture or load immutable **WARC** (ISO 28500) web-archive snapshots (from live capture or Wayback Machine CDX snapshots) into `data/heritage_raw/warc/`.
2. Extract main body content and structural Markdown via **`Trafilatura`**, stripping navigation chrome, ad wrappers, and dead forum sidebars while preserving code blocks, tables, and original URLs + capture timestamps.
3. Distill extracted facts into attributed cleanroom Markdown entries under `knowledge/`.

### 2.2 Engineer Binders & Manual OCR (`Docling`)
Targets scanned **former Chrysler engineer binders**, Factory Technical Bulletins (TSBs), and service schematic supplements:
1. Place raw PDFs / page scans in `data/heritage_raw/pdfs/`.
2. Run **`Docling`** PDF layout + OCR-to-Markdown conversion to preserve multi-column tables (e.g., 60-way pinout matrices, calibration lookup tables, 68HC11 opcode notes) and exact source page numbers (`page_ref`).
3. Distill verified cleanroom specifications into `knowledge/` with explicit page/binder citations.

---

## 3. Structured YAML Frontmatter & 3-Tier Trust Badges

Every Markdown digest stored in `knowledge/` MUST open with a machine-validated YAML frontmatter block:

```yaml
---
doc_id: "smec-60way-pin11-bootstrap"
title: "SMEC vs. 1990-93 TBI SBEC 60-Way Pin 11 Harness Divergence"
trust_tier: "Tier 1"
source_type: "FACTORY_TECH_BULLETIN"
source_citation: "Chrysler 1989/1991 Powertrain Diagnostic Wiring Supplement & MiniMopar Pinout Archive"
warc_capture_date: "2025-11-04T00:00:00Z"
page_ref: "Sec 8W-30 / pp. 14-16"
ecu_families: ["SMEC_DUAL_BOARD", "SBEC_SINGLE_BOARD"]
hex_symbols: ["0x12", "0xF2", "0xF4", "$8000"]
cleanroom_status: "DISTILLED_ATTRIBUTED"
verified_by: "human_maintainer"
---
```

### 3-Tier Trust Badge Policy

| Trust Badge | Authoritative Scope | Agent & UI Authority Rule |
| :---: | :--- | :--- |
| **`Tier 1`** | **Chrysler Engineer Notes, Factory Service Manuals (FSM), & Factory Technical Bulletins** | Primary ground truth for 60-way wiring, ECU electrical limits, factory tolerances, and stock timing/fuel constraints. |
| **`Tier 2`** | **Curated Community Reference Guides** (*MiniMopar*, *Turbonator* `.asm`/`.tpl` docs, vetted *Turbo-Mopar* tables) | Trusted reference for SCI protocol behavior (`0xF2`/`0xF4`), Turbonator source offsets, and verified cross-year wiring notes when no `Tier 1` conflict exists. |
| **`Tier 3`** | **Archival Forum Threads & Anecdotal Tuning Logs** (*TurboDodge*, *SDAC* mailing lists/threads) | Diagnostic hypotheses & historical troubleshooting tips ONLY. **NEVER** sufficient alone to authorize Pin 11 voltage, unverified `0xF2`/`0xF4` RAM offsets, or safety gate bypasses. |

---

## 4. Hybrid Search Engine (`SQLite FTS5` + `sqlite-vec` + `RRF`)

Pure dense vector search frequently fails on embedded firmware queries because semantic embeddings smooth away exact hex literals (`$8000`, `0xF2`, `0xF4`, `$FFC0`), 68HC11 opcodes (`LDAA`, `MUL`, `IDIV`, `BSET`), and pin identifiers (`Pin 11`, `Pin 2`). Conversely, pure keyword matching misses symptom phrasing (e.g., *"hot restart fuel boil lean surge"*). LogicAIDE fuses both inside a single local SQLite database (`data/heritage_index.sqlite`).

### 4.1 Lexical Branch (`SQLite FTS5` + BM25)
- Uses an `FTS5` virtual table configured with `tokenize = 'unicode61 tokenchars "$_x"'` so `$8000`, `0xF2`, `0x1030`, `Pin_11`, and `ADCTL` index as **atomic, exact hex/symbol tokens** alongside prose terms.
- Ranks exact matches with SQLite BM25 weights prioritizing `title`, `hex_symbols`, and `body`.

### 4.2 Semantic Branch (`sqlite-vec`)
- Stores chunk embeddings in a `sqlite-vec` (`vec0`) virtual table keyed by matching `chunk_rowid` and `doc_id`.
- Evaluates cosine / L2 nearest-neighbor similarity for symptom descriptions, hardware swap questions, and conceptual queries.

### 4.3 Reciprocal Rank Fusion (`RRF`)
Independent top-$N$ lists from `FTS5` and `sqlite-vec` are merged without fragile score normalization via **Reciprocal Rank Fusion ($k = 60$)**:
$$\text{RRF}(d) = \frac{w_{\text{lex}}}{k + \text{rank}_{\text{FTS5}}(d)} + \frac{w_{\text{vec}}}{k + \text{rank}_{\text{vec}}(d)}, \qquad k = 60$$

- **Deterministic Tie-Breaking & Trust Surfacing**: Ties break toward higher provenance (`Tier 1` > `Tier 2` > `Tier 3`), and every returned passage explicitly displays its `trust_tier` badge, `source_citation`, and `page_ref` / `warc_capture_date`.
- **Zero-Hallucination Abstention (`No Verified Source Match`)**: If neither branch yields a verified match above configured lexical/similarity thresholds (or only `Tier 3` speculation exists for a hex offset or connector pin), the search tool returns **`No Verified Source Match`** rather than fabricating an address or pin assignment (`AGENTS.md`).

---

## 5. CLI & `chrysler-heritage` Interface

```bash
# Ingest local WARC/HTML or engineer binder PDFs into gitignored data/heritage_raw/
uv run logicaide heritage ingest --source-type warc --input data/heritage_raw/warc/minimopar.warc.gz
uv run logicaide heritage ingest --source-type docling --input data/heritage_raw/pdfs/smec_notes.pdf

# Validate YAML frontmatter & rebuild hybrid FTS5 + sqlite-vec SQLite index from knowledge/
uv run logicaide heritage index --verify-frontmatter

# Run hybrid RRF query (CLI parity with chrysler-heritage FastMCP)
uv run logicaide heritage query "Pin 11 SBEC ground vs SMEC bootstrap" --min-tier 2
```

- **MCP Parity (`chrysler-heritage`)**: Exposes `hybrid_search_heritage`, `lookup_60way_pin`, `lookup_sci_offset`, and `get_provenance_card` read-only over `knowledge/` and `data/heritage_index.sqlite`.
- **Prose Reader Compatibility**: Returned citations pair directly with `.quiet-ui.md` split-reader layouts (`>= 60%` clean prose column with visible `Tier 1`/`Tier 2`/`Tier 3` provenance pills).
