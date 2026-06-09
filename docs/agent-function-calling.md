# Function-Calling Compatibility

The Telnyx OpenAPI spec (`openapi/spec3.json`) is consumed not only by SDK
generators but by **LLM function-calling / tool-use** pipelines (OpenAI tools,
Anthropic tools, LangChain, and Ora's API-readiness scanner). Those pipelines
convert each OpenAPI **operation** into exactly one callable **tool**, so a few
properties have to hold for the conversion to succeed.

This document describes the contract, how to validate it, and the current
status. The contract is enforced by the ruleset in
[`redocly.yaml`](../redocly.yaml).

## Why it matters

When an agent runtime ingests this spec, each operation becomes a function the
model can call:

| OpenAPI element        | Function-calling role                     |
| ---------------------- | ----------------------------------------- |
| `operationId`          | Tool/function **name** (must be unique)   |
| `summary`/`description`| Tool description used for **selection**   |
| `parameters[]`         | Function **arguments** (typed + described)|
| `requestBody.schema`   | Function **arguments** (body)             |
| `2xx` response schema  | Result the agent **parses**               |
| `4xx`/error schema     | Structured **error recovery**             |

If two operations share an `operationId`, they collide into one function name
and most generators silently drop one of the tools. If a parameter has no
schema, the runtime cannot type the argument. If an operation has no summary,
the model cannot reliably decide when to call it.

## The contract

Enforced as `error` (a regression here breaks tool generation):

- **`spec`** — the document is structurally valid OpenAPI and parses.
- **`operation-operationId`** — every operation exposes a tool name.
- **`operation-operationId-unique`** — tool names are globally unique.
- **`operation-operationId-url-safe`** — tool names are safe identifiers.
- **`operation-summary`** — every operation has a concise, selectable summary.
- **`operation-parameters-unique`** — no duplicate argument names.
- **`operation-2xx-response`** — a typed success result exists.
- **`no-identical-paths`** / **`path-declaration-must-exist`** — unambiguous routing.

Tracked as `warn` (enhancements, already broadly satisfied):

- **`operation-description`** — long-form description in addition to the summary.
- **`operation-4xx-response`** — a modelled error response for recovery.
- **`rule/parameter-has-description`** — every parameter carries a description.

## How to validate (read-only)

```bash
# From the repo root — auto-discovers redocly.yaml:
npx @redocly/cli@latest lint openapi/spec3.json

# Or the convenience wrapper:
./scripts/validate-function-calling.sh
```

Both commands are read-only and never modify spec files.

## Current status (OpenAPI 3.1.0)

A scan of `openapi/spec3.json` shows the spec is in strong shape for
function-calling:

- **734 paths / 1082 operations**; every operation has an `operationId` and a
  `summary`.
- Every inline parameter is typed (has a `schema`/`content`) and described.
- Every request body is typed; every operation has a `2xx` response.

### Known gap: one duplicate `operationId`

The error-level ruleset currently surfaces exactly one violation:

- `operationId: ListPhoneNumbers` is used by **both**:
  - `GET /phone_numbers`
  - `GET /v2/whatsapp/phone_numbers`

These two operations collapse into a single `ListPhoneNumbers` tool, so one of
them is dropped by function-calling generators. The fix is to give the WhatsApp
operation a distinct id (for example `ListWhatsappPhoneNumbers`).

**This must be fixed in the internal source-of-truth, not here.** The specs
under `openapi/` are generated; a hand-edit would be overwritten on the next
sync (see [`AGENTS.md`](../AGENTS.md)). Once the source rename lands and syncs,
this lint passes clean.

## References

- Ruleset: [`redocly.yaml`](../redocly.yaml)
- Repo agent guide: [`AGENTS.md`](../AGENTS.md)
- Cross-repo plan (Phase 4 — OpenAPI & Function Calling):
  https://github.com/team-telnyx/win-the-bot/blob/main/cases/ora-agent-readiness/cross-repo-execution-plan.md
- Live Ora score: https://ora.run/score/telnyx.com
