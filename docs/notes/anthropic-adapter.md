# Anthropic adapter

`signalforge.providers.anthropic_provider.AnthropicProvider` maps the neutral boundary onto the
**Anthropic Messages API** using the official `anthropic` Python SDK.

- SDK verified: **anthropic 1.5.0** (installed and introspected on 2026-09-10; depends on httpx2).
- Client: `anthropic.AsyncAnthropic(max_retries=1, timeout=<settings>)`. The key is read by the
  SDK from `ANTHROPIC_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`); SignalForge never touches the value.
- Model: `SIGNALFORGE_ANTHROPIC_MODEL`, required. No default model id exists in the code.
- Extra: `pip install -e ".[anthropic]"` (`anthropic>=1,<2`).

## Request mapping (`complete`)

| neutral                              | Anthropic Messages API                                            |
|--------------------------------------|-------------------------------------------------------------------|
| `SystemPrompt.text`                  | `system`                                                          |
| `UserMessage`                        | `{"role": "user", "content": [{"type": "text", ...}]}`            |
| `AssistantMessage.text`              | `{"type": "text"}` block                                          |
| `AssistantMessage.tool_requests`     | `{"type": "tool_use", "id", "name", "input"}` blocks              |
| `AssistantMessage.opaque`            | thinking / redacted_thinking blocks echoed verbatim, first        |
| `ToolResultsMessage`                 | user message of `{"type": "tool_result", "tool_use_id", "content", "is_error"}` blocks |
| `ToolSpec`                           | `{"name", "description", "input_schema"}`                         |
| tools present                        | `tool_choice={"type": "auto"}` (forced choice is never used)      |
| `GenerationConfig.max_output_tokens` | `max_tokens` (the larger of settings and config)                  |
| `GenerationConfig.timeout_seconds`   | per-request `timeout` (the smaller of settings and config)        |
| effort (`SIGNALFORGE_EFFORT`)        | `output_config={"effort": ...}` when set                          |

The API requires strict user/assistant alternation. The orchestrator produces consecutive
user-role items (seed, status block, tool results followed by the next status block), so
`to_messages` merges consecutive same-role items into one message with several content blocks.
An assistant message with no content becomes a single `(no content)` text block.

## Response mapping

- `text` blocks become `ModelTurn.text` (joined).
- `tool_use` blocks become `ToolRequest(id, name, arguments=input)`; several blocks are parallel requests.
- `thinking` / `redacted_thinking` blocks become `ModelTurn.opaque` (in memory only; see the security note).
- `usage.input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`
  become `ModelUsage(reported=True, input, output, cached_input, cache_write)`.
- `stop_reason`: `end_turn`, `tool_use`, `max_tokens`, `stop_sequence`, `pause_turn` are passed
  through; `refusal` raises `ProviderFailure("refusal")` including `stop_details.category` when
  present; `model_context_window_exceeded` raises `ProviderFailure("context_exhausted")`.

## Structured output (`generate_structured`)

`client.messages.parse(**request, output_format=ReportDraft)` and read
`ParsedMessage.parsed_output`. The SDK transforms the Pydantic schema for the API (unsupported
constraints such as `minLength` are folded into descriptions) and validates the JSON against the
Pydantic model on the way back. If `parsed_output` is absent the adapter falls back to parsing the
text block as JSON; a validation failure is returned as `StructuredResult.parse_error` (the
orchestrator then runs a repair round) rather than raised. No tools are sent on report calls.

## Errors

Vendor exceptions are classified by `providers/errors.classify_exception` (class name, then HTTP
status, then message markers) into: `authentication` (401), `permission` (403), `invalid_model`
(404 mentioning the model), `rate_limited` (429), `context_exhausted` (400/413 with "too long" or
"context" markers), `invalid_request` (other 4xx), `timeout`, `network`, `server_error` (5xx),
`unknown`. Messages are redacted. Retryable: rate limit, timeout, network, server error.
Setup errors are raised at construction time without any request: `missing_sdk`, `missing_api_key`
(with the reminder that a Claude subscription is not an API credential), `missing_model`,
`invalid_request` (bad effort level).

## Tests (no network)

`tests/test_anthropic_adapter.py` uses `tests/vendor_fakes.FakeAnthropicClient`, a stand-in for
`AsyncAnthropic` that records every request kwargs dict and returns prebuilt Anthropic-shaped
responses. Covered: alternation merge, tool schema conversion, multi tool_use parsing, thinking
blocks kept opaque, usage fields, refusal and context-window stop reasons, `parse` structured
output and both fallbacks, all error categories (with fake and, when installed, real SDK
exception classes), redaction, missing SDK (`sys.modules["anthropic"] = None`), missing key and
model. `tests/test_provider_contract.py` runs a complete investigation through the adapter with
the scripted playbook acting as the fake Anthropic "model" and checks the result equals the
scripted run.
