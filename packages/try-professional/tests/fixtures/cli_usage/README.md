Usage evidence checked on 2026-11-16:

- Codex CLI 0.169.0: `codex-turn.jsonl` uses the upstream JSONL format, with
  chosen counts to exercise caching. It is a format fixture, not a live model
  measurement. `exec_events.rs` defines the fields, including cache writes.
  `event_processor_with_jsonl_output.rs`, `usage_from_last_total`, emits a
  cumulative thread total at `turn.completed`. `protocol.rs`, `non_cached_input`,
  subtracts cached input from input, proving that input already includes reads.
  Reasoning is part of output; it is not added again.
  Sources pinned to the installed version:
  https://raw.githubusercontent.com/openai/codex/rust-v0.169.0/codex-rs/exec/src/exec_events.rs
  https://raw.githubusercontent.com/openai/codex/rust-v0.169.0/codex-rs/exec/src/event_processor_with_jsonl_output.rs
  https://raw.githubusercontent.com/openai/codex/rust-v0.169.0/codex-rs/protocol/src/protocol.rs
- Antigravity CLI `agy --version` reported 1.5.6. The two JSON fixtures preserve
  usage and turn counts printed by two real `agy -p --output-format json`
  calls, the second using `--conversation` to resume the first. Session ids are
  replaced with a fixed fixture id. Input rises from 13,615 to 15,930 and cache
  reads from zero to 11,566. The 2,315 input delta cannot include the 11,566 cache
  read delta. These are cumulative conversation counters; cache reads are
  excluded from input. The second normalized input is 27,496 and output is 92.
  Its reported `total_tokens` (16,022) excludes the cache reads. Thinking counts
  are not added to output a second time. Source: installed CLI stdout from the
  first and resumed probes, captured on the date above.
- Claude Code `claude --version` reported 2.1.325. `claude-result.json` preserves
  the installed CLI's successful JSON result against a local HTTP/SSE provider
  fixture. The provider supplied 23 uncached input, 970 cache reads, 10 cache
  writes and 7 output tokens, and the CLI preserved those four counters in its
  result (and its model breakdown). Normalized input is 1,003, not 23.
  Session ids are replaced and unrelated result metadata is omitted. Source:
  installed CLI stdout captured against that local provider on the date above.
  A live provider smoke check returned HTTP 403 for the configured account, so
  a successful real Anthropic model call remains unverified.

Review corrections checked on 2026-11-16:

- `claude-error.json` retains the result flags, default counters and empty
  model breakdown from the reviewer's installed Claude Code 2.1.325 stdout,
  `reviewer-claude-code.stdout`, which exited 1 on HTTP 403. Its subtype says
  success even though is_error is true and terminal_reason is api_error.
  Account text and unrelated metadata are omitted. These zeros do not prove
  provider usage. The successful local-provider fixture above now retains its
  matching per-model counters, establishing the provenance absent in this error.
- `codex-default.jsonl` is a format fixture for the pinned Codex source above:
  usage_from_last_total returns Usage::default() when last_total_token_usage
  is absent. A completed event and zero counters alone cannot establish that a
  provider reported zero usage. Positive supported snapshots establish counts;
  a failed process leaves their completeness uncertain.
- A successful Claude result with a matching nonempty model breakdown can
  support zero counts. The all-zero variant in the tests is controlled contract
  data, not a live zero-token provider measurement. Repeated nonzero cumulative
  Antigravity snapshots establish genuine zero continuation deltas.

These are usage contracts reported by the CLIs, not independent billing
measurements. Missing fields, a text-only total, malformed counters and a lost
or incomplete result cannot establish the exact requested split. No fixture
contains credentials, personal text or a model spending limit.
