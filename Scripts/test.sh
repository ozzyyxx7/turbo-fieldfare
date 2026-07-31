#!/usr/bin/env bash
# Serial test runner. Shared Metal state makes in-process parallel Swift tests
# unreliable. A full run also executes the dependency-free Codex bridge tests.
# Pass any extra arguments through for a focused Swift run, for example --filter.

set -euo pipefail

if [[ "${1:-}" == "--package-path" ]]; then
  shift 2
fi

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$script_directory/.."
swift test --no-parallel "$@"

if (( $# == 0 )); then
  python3 -m unittest discover \
    -s Integrations/Codex/supergemma-local/tests \
    -p 'test_runtime.py' \
    -v
  node --test Integrations/Codex/supergemma-local/tests/mcp-server.test.mjs
fi
