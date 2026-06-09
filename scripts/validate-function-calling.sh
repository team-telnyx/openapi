#!/usr/bin/env sh
# Read-only function-calling compatibility check for the Telnyx OpenAPI spec.
#
# Lints the spec against the ruleset in ../redocly.yaml, which asserts the
# properties LLM function-calling / tool-use generators depend on (unique
# operationIds, typed + described parameters, summaries, typed responses).
#
# This script NEVER modifies the spec. The specs under openapi/ are generated
# from an internal source-of-truth (see AGENTS.md); fix lint findings there.
#
# Usage:
#   ./scripts/validate-function-calling.sh [path-to-spec]   # default: openapi/spec3.json
#
# A non-zero exit means a function-calling-relevant rule failed. See
# docs/agent-function-calling.md for the contract and any known gaps.
set -eu

SPEC="${1:-openapi/spec3.json}"

if [ ! -f "$SPEC" ]; then
  echo "error: spec not found: $SPEC" >&2
  exit 2
fi

echo "Linting '$SPEC' for LLM function-calling compatibility (read-only)..."
exec npx --yes @redocly/cli@latest lint "$SPEC"
