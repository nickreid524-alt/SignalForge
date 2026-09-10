# OpenAI adapter

`signalforge.providers.openai_provider.OpenAIProvider` maps the neutral boundary onto the
**OpenAI Responses API** using the official `openai` Python SDK. Chat Completions is not used.

- SDK verified: **openai 3.12.0** (installed and introspected on 2026-09-10; depends on httpx2).
- Client: `openai.AsyncOpenAI(max_retries=1, timeout=<settings>)`. The key is read by the SDK
  from `OPENAI_API_KEY`; SignalForge never touches the value.
- Model: `SIGNALFORGE_OPENAI_MODEL`, required. No default model id exists in the code.
- Extra: `pip install -e ".[openai]"` (`openai>=3,<4`).

## Request mapping (`complete`)

| neutral                              | OpenAI Responses API                                                    |
|--------------------------------------|-------------------------------------------------------------------------|
| `SystemPrompt.text`                  | `instructions`                                                          |
| `UserMessage`                        | `{"role": "user", "content": ...}` input item                           |
| `AssistantMessage.opaque`            | `reasoning` items echoed verbatim (`id`, `encrypted_content`), first    |
| `AssistantMessage.text`              | `{"role": "assistant", "content": ...}`                                 |
| `AssistantMessage.tool_requests`     | `{"type": "function_call", "call_id", "name", "arguments": <JSON string>}` |
| `ToolResultsMessage`                 | `{"type": "function_call_output", "call_id", "output"}` items           |
| `ToolSpec`                           | `{"type": "function", "name", "description", "parameters", "strict": false}` |
| tools present                        | `tool_choice="auto"`, `parallel_tool_calls=True`                         |
| `GenerationConfig.max_output_tokens` | `max_output_tokens` (the larger of settings and config)                 |
| `GenerationConfig.timeout_seconds`   | per-request `timeout` (the smaller of settings and config)              |
| always                               | `store=False`; therefore `include=["reasoning.encrypted_content"]`       |
| effort (`SIGNALFORGE_EFFORT`)        | `reasoning={"effort": ...}` when set                                    |

`strict=False` keeps the MCP tool schemas as published (strict mode would force every property
to be required and forbid additional properties, which does not match the server's optional
parameters). Argument validation against the MCP input schema happens in `ActionPolicy` anyway.

## Response mapping

- `message` items: `output_text` parts become `ModelTurn.text`; a `refusal` part raises
  `ProviderFailure("refusal")`.
- `function_call` items become `ToolRequest(id=call_id, name, arguments=json.loads(arguments))`;
  unparseable arguments raise `ProviderFailure("malformed_output")`.
- `reasoning` items become `ModelTurn.opaque` as `{type, id, encrypted_content, summary: []}`; any
  summary text the API might include is discarded.
- `usage.input_tokens`, `usage.output_tokens`, `usage.input_tokens_details.cached_tokens`,
  `usage.output_tokens_details.reasoning_tokens` become `ModelUsage`.
- `status == "incomplete"`: `incomplete_details.reason == "max_output_tokens"` becomes
  `stop_reason="max_output_tokens"` (the turn is still returned); `content_filter` raises
  `ProviderFailure("refusal")`. Otherwise `stop_reason` is `tool_use` when function calls were
  returned, else `end_turn`.

## Structured output (`generate_structured`)

`client.responses.parse(**request, text_format=ReportDraft)` and read `output_parsed`. The SDK
converts the Pydantic model to a strict JSON schema for the API and validates the reply. If
`output_parsed` is absent the adapter parses `output_text` as JSON; a validation failure is
returned as `StructuredResult.parse_error` for the orchestrator's repair loop. No tools are sent on
report calls.

## Errors

Same normaliser as the Anthropic adapter (`providers/errors.py`). OpenAI-specific class names
handled by name: `LengthFinishReasonError` becomes `context_exhausted`,
`ContentFilterFinishReasonError` becomes `refusal`, `PermissionDeniedError` becomes `permission`,
`NotFoundError` mentioning the model becomes `invalid_model`. Setup errors (`missing_sdk`,
`missing_api_key` with the reminder that a ChatGPT subscription is not an API credential,
`missing_model`, invalid `reasoning_effort`) are raised at construction time without any request.

## Tests (no network)

`tests/test_openai_adapter.py` uses `tests/vendor_fakes.FakeOpenAIClient`, a stand-in for
`AsyncOpenAI` that records request kwargs and returns Responses-shaped objects. Covered: input
item conversion (including reasoning echo and JSON-string arguments), tool schema conversion,
multi function_call parsing, reasoning items kept opaque without summaries, usage details,
refusal parts, incomplete responses (both reasons), malformed arguments, `parse` structured output
and fallbacks, error categories with fake and real SDK exception classes, missing SDK
(`sys.modules["openai"] = None`), missing key and model. `tests/test_provider_contract.py` runs
a complete investigation through the adapter with the scripted playbook acting as the fake OpenAI
"model" and checks the result equals the scripted run.
