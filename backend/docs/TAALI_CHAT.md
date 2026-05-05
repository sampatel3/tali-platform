# Taali Chat

In-product agentic chat UI inside Tali. Replaces the static "Candidates"
tab with a conversational interface that searches, compares, and reasons
about candidates.

The chat backend is just a thin glue layer over the **same MCP tool
surface** that Claude Desktop / claude.ai use externally. There is one
canonical set of tools (`backend/app/mcp/handlers.py`); both the public
MCP HTTP server (`/mcp`) and the internal chat endpoint
(`/api/v1/taali-chat/*`) call them. Add a tool once, both surfaces light up.

## Architecture

```
React app  ──HTTPS──▶  /api/v1/taali-chat/turn  ──▶  Anthropic API
                              │                          │
                              │                          ▼ tool_use
                              │                  app.mcp.handlers
                              │                  (Postgres + Neo4j)
                              ▼
                       taali_chat_messages
                       (persistent transcripts)
```

## API

### `POST /api/v1/taali-chat/turn`

Stream one turn back to the client. **Wire format**: AI SDK Data Stream
Protocol (newline-delimited tagged frames over `text/event-stream`).
Compatible out-of-the-box with [`@assistant-ui/react`](https://www.assistant-ui.com)
and Vercel `useChat`.

**Request body**:

```json
{
  "message": "find me an aws glue engineer with 5+ years",
  "conversation_id": null
}
```

`conversation_id` may be `null` (new conversation) or an existing id
from `GET /conversations`. The first response frame is a `2:` data
frame with the assigned `conversation_id` so the frontend can pin it.

**Frame types** (subset of the AI SDK Data Stream Protocol — full list
in [`app/taali_chat/streaming.py`](../app/taali_chat/streaming.py)):

| Prefix | Meaning |
|---|---|
| `2:` | Server-side data frame. First one carries `{conversation_id}`. |
| `0:` | Streamed text token. |
| `9:` / `a:` / `b:` | Tool call lifecycle: start / args delta / end. |
| `c:` | Tool result. `{toolCallId, result, isError?}`. |
| `e:` | Step-finish (per Anthropic round, when there are multiple). |
| `d:` | Message-finish. Terminal frame. |
| `3:` | Error. |

### `GET /api/v1/taali-chat/conversations`

Sidebar listing for the current user/org. Returns up to 200 rows
ordered by `updated_at desc` then `created_at desc`.

### `GET /api/v1/taali-chat/conversations/{id}`

Full transcript of one conversation. Each message's `content` is an
Anthropic-shaped list of content blocks (text / tool_use / tool_result)
so the frontend can replay exactly what the model saw.

### `PATCH /api/v1/taali-chat/conversations/{id}`

Body: `{"title": "AWS Glue search"}`. Renames the conversation.

### `DELETE /api/v1/taali-chat/conversations/{id}`

Soft-delete (sets `archived_at`). Hidden from list endpoint thereafter.

## Tool catalogue

The chat tool catalogue is defined in
[`app/taali_chat/tool_registry.py`](../app/taali_chat/tool_registry.py)
in Anthropic tool format. It mirrors the MCP server's tool list —
same names, same arguments, same payloads. See [MCP_SERVER.md](MCP_SERVER.md#tool-surface-read-only)
for the full table.

**System prompt** is the first prompt-cache breakpoint on every turn
(see [`app/taali_chat/system_prompt.py`](../app/taali_chat/system_prompt.py)).
Bumping it invalidates the cache for every active conversation, so keep
edits intentional.

## Multi-tenancy & auth

Reuses fastapi-users JWT. Every endpoint resolves `current_user` and
filters by `(user_id, organization_id)` on conversations and
`organization_id` on the in-process tool dispatch. Cross-org isolation
is covered by `tests/test_taali_chat_routes.py::test_cross_user_cannot_see_others_conversation`.

## Billing

Every chat turn records a single `UsageEvent` against the
`Feature.TAALI_CHAT` pricing entry (2× markup, 0.10× cache-hit
multiplier — see `app/services/pricing_service.py`). Caching costs
flow through Anthropic's prompt cache; the billing meter consumes
`cache_read_input_tokens` / `cache_creation_input_tokens` from each
Anthropic response.

## Safety guards

- **`MAX_TOOL_ROUNDS = 8`** per turn — caps runaway loops.
- **`MAX_TOKENS_PER_TURN = 4096`** — large enough for comparison tables,
  small enough that one bad turn can't drain credits.
- All tool errors are caught and converted to `tool_result` frames with
  `isError: true` rather than crashing the stream.
- Empty messages are rejected without an Anthropic call.

## Frontend integration

**Don't reinvent the chat UI**. Use [`@assistant-ui/react`](https://www.assistant-ui.com)
or [Vercel AI SDK](https://sdk.vercel.ai); both speak the AI SDK Data
Stream Protocol natively, so they'll consume our endpoint without a
custom adapter.

Recommended:

```tsx
import { useChat } from '@assistant-ui/react';

const chat = useChat({
  api: '/api/v1/taali-chat/turn',
  headers: { Authorization: `Bearer ${jwt}` },
  body: { conversation_id: currentId },
});
```

Tool-call rendering is first-class in assistant-ui — register a custom
`ToolUI` per tool name to render search results as candidate-card grids,
comparison tables, etc. Layout per [`copilot.html`](../../design/) in
the design handoff.

## Files

- `app/taali_chat/__init__.py` — public surface (`run_chat_turn`).
- `app/taali_chat/service.py` — Anthropic loop + persistence + frame emission.
- `app/taali_chat/streaming.py` — AI SDK Data Stream Protocol adapter.
- `app/taali_chat/system_prompt.py` — cached system prompt.
- `app/taali_chat/tool_registry.py` — Anthropic tool catalogue + dispatcher.
- `app/domains/taali_chat/routes.py` — HTTP routes.
- `app/models/taali_chat_conversation.py` — conversation model.
- `app/models/taali_chat_message.py` — message model.
- `alembic/versions/055_add_taali_chat_tables.py` — schema migration.
- `tests/test_taali_chat_handlers.py` — direct handler unit tests.
- `tests/test_taali_chat_service.py` — Anthropic-loop tests with fake SDK.
- `tests/test_taali_chat_routes.py` — HTTP route tests.
