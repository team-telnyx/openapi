# AGENTS.md

This repository participates in Telnyx's "Win the Bot" agent-readiness initiative. AI coding agents working in this repo should read this file first.

## Purpose
This is the public Telnyx OpenAPI repository. The spec is consumed by SDK generators, agent function-calling tools, and Ora's API-readiness scanners. Specs in this repo are GENERATED from an internal source — do not hand-edit generated files.

The generated spec files live under `openapi/`:
- `openapi/spec3.json`, `openapi/spec3.yml` — OpenAPI 3.0 spec (generated)
- `openapi/spec3_stainless.yml` — Stainless-flavored spec (generated)
- `openapi/spec2.json`, `openapi/spec2.yml` — legacy placeholders

## Safe Commands
This repo has no build system or CI of its own — it only ships generated artifacts. Agents that need to validate a spec locally can use Redocly or Spectral without modifying the repo:

```bash
# Validate / lint the OpenAPI 3 spec (read-only)
npx @redocly/cli@latest lint openapi/spec3.json
npx @stoplight/spectral-cli@latest lint openapi/spec3.json

# Render the spec locally to inspect changes
npx @redocly/cli@latest preview-docs openapi/spec3.json
```

Do not run formatters, codegen, or "fix" commands against the spec files — they will be overwritten on the next sync from the internal source-of-truth.

## Testing Strategy
- Validate spec parseability locally before opening a PR (see commands above).
- Lint operations for unique `operationId`s, typed parameters, error schemas, and pagination consistency.
- If a lint failure points at a generated file, the fix belongs in the internal source repo, not here.

## PR Expectations
- Branch from `master`; small, focused PRs.
- Do not hand-edit generated YAML/JSON specs — file an issue against the internal source-of-truth instead.
- README maintainer is @nicktimko; recent sync PRs come from @ankitTelnyx — request review from one of them.
- Keep PRs scoped to repo-level docs/tooling (like this file). Spec changes should flow through the internal generator.

## API & Security Boundaries
- No secrets in this repo.
- Schemas in this repo are public; do not paste internal-only path or auth details into issues, PRs, or generated diffs.
- Do not add internal-only endpoints, hostnames, or auth flows when proposing edits.

## Agent-Readiness Tracking
- Cross-repo plan: https://github.com/team-telnyx/win-the-bot/blob/main/cases/ora-agent-readiness/cross-repo-execution-plan.md (Phase 4 covers this repo)
- Score impact map: https://github.com/team-telnyx/win-the-bot/blob/main/cases/ora-agent-readiness/score-impact-map.md
- Live Ora score: https://ora.run/score/telnyx.com

## Out of Scope Without Human Review
- Do not modify generated spec files directly (`openapi/spec*.{json,yml}`).
- Do not change versioning or path conventions without coordinating with API platform owners.
- Do not add CI that mutates the spec files in-place.
