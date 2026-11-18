# server/

FastMCP app. `app.py` is the composition root: it wires accessor -> repository -> service and
registers each dataset's `tools/`, `prompts/` and `resources/` modules (`register(mcp, ...)`).

Layers (each only calls the one below; one `private_residential/` package per dataset, e.g.
`src/sg_mcproperty/server/private_residential/` (`filters.py`, `repository.py`, `service.py`)):

| Layer | Lives in | Owns |
|---|---|---|
| MCP adapters | `server/tools`, `server/prompts`, `server/resources` | signatures, docstrings, one-line calls into the service. No SQL, no rules |
| Service | `private_residential/` package | validation, limits, result shaping |
| Repository | `private_residential/` package | SQL templates and parameters |
| Accessor | `accessor.py` | the ClickHouse connection |

- Read-only: the accessor defaults to `readonly=True`. Parameterise SQL with `{name:Type}`;
  only the trusted table name and fixed fragments go in `string.Template` fields.
- Tools return typed results (TypedDicts in the `private_residential/` package), bounded in size (aggregate
  in SQL, don't dump rows). Type hints become the output schema.
- Docstrings and type hints on tools become the schema the LLM sees; keep them precise
  (units, example values, optional filters). Normalise and validate inputs in the service or
  filter objects, not in the tool body.
- Prompts return instructions that name existing tools. Resources are reference text or small
  status documents the client reads.
- Plain MergeTree tables need no `FINAL`. Use it only on a ReplacingMergeTree.
- stdio is the default transport (`sg-property-mcp`). Try tools with
  `python -m sg_mcproperty.server.dev_client`.
