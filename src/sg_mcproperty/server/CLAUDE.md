# server/

FastMCP app. `app.py` builds the `FastMCP` instance and calls each `tools/<topic>.register(mcp)`.

- Read-only: always `get_client(readonly=True)`. Parameterise SQL with `{name:Type}`.
- Tools return JSON-friendly lists/dicts, bounded in size (aggregate in SQL, don't dump rows).
- Docstrings and type hints become the tool schema the LLM sees; keep them precise
  (units, example values, optional filters). Normalise inputs (e.g. upper-case town names)
  inside the tool.
- Use `FINAL` on ReplacingMergeTree tables when counts/medians must be exact.
- stdio is the default transport (`sg-property-mcp`).
