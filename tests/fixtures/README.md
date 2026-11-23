# `tests/fixtures/` — GPS-Stripped Stimulus Slices & Sanitized Payloads

`tests/fixtures/` holds compact, deterministic test inputs for `m68hc11` replay, Python Twin bit-exact parity checks, and telemetry parser unit tests.

## Fixture Standards
- **1–3 Second Stimulus Slices**: Focused excerpts extracted around `VEHICLE_SCI` anomaly bookmarks or targeted `RPM x MAP x TPS x ACT` load cells.
- **Mandatory GPS Privacy Stripping**: **Never** commit raw latitude, longitude, elevation tracks, or home/route identifiers. Run the telemetry sanitizer before exporting any fixture from `data/drives/`.
- **Zero Copyrighted OEM ROMs**: Do not place full `32 KB` OEM `.bin` images here; use synthetic register vectors, anonymized SCI frame captures, and small JSON/Parquet slices.
- **LF Line Endings**: Keep all text/JSON fixtures strictly LF (`\n`) per `.gitattributes`.
