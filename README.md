# posthog-ff-mcp

A tiny, **read-only** MCP server that lets Claude read PostHog feature-flag state and
metadata. It wraps PostHog's REST API — listing flags, reading a flag's full
definition, checking staleness, and explaining how flags evaluate for a person. It
never creates, updates, or deletes anything.

It's a single Python file (`server.py`) with inline dependencies, so it runs locally
with [`uv`](https://docs.astral.sh/uv/) and no build step.

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) installed.
- A PostHog **personal API key** scoped to at least `feature_flag:read` for your
  project. Create one at `<your-host>/settings/user-api-keys`.

## Configuration

All config is via environment variables (no defaults):

| Variable | Required | Example | Notes |
| --- | --- | --- | --- |
| `POSTHOG_HOST` | yes | `https://eu.posthog.com` | App host. EU: `eu.posthog.com`, US: `us.posthog.com`, or your self-hosted URL. |
| `POSTHOG_PROJECT_ID` | yes | `13105` | Numeric project id (the number in the app URL `/project/<id>`). |
| `POSTHOG_PERSONAL_API_KEY` | yes | `phx_…` | Personal API key with `feature_flag:read`. |

See [`.env.example`](./.env.example).

## Run it locally

```bash
POSTHOG_HOST=https://eu.posthog.com \
POSTHOG_PROJECT_ID=13105 \
POSTHOG_PERSONAL_API_KEY=phx_your_key \
uv run server.py
```

The first run resolves dependencies automatically; the server then speaks MCP over
stdio.

## Add to Claude Code

```bash
claude mcp add posthog-ff \
  --env POSTHOG_HOST=https://eu.posthog.com \
  --env POSTHOG_PROJECT_ID=13105 \
  --env POSTHOG_PERSONAL_API_KEY=phx_your_key \
  -- uv run --directory /ABSOLUTE/PATH/TO/posthog-ff-mcp server.py
```

Replace `/ABSOLUTE/PATH/TO/posthog-ff-mcp` with this repo's absolute path.

## Add to Claude Desktop

Add this to `claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "posthog-ff": {
      "command": "uv",
      "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/posthog-ff-mcp", "server.py"],
      "env": {
        "POSTHOG_HOST": "https://eu.posthog.com",
        "POSTHOG_PROJECT_ID": "13105",
        "POSTHOG_PERSONAL_API_KEY": "phx_your_key"
      }
    }
  }
}
```

Restart Claude Desktop afterward.

## Tools

| Tool | What it does | Key arguments |
| --- | --- | --- |
| `list_feature_flags` | List flags as compact summaries, with paging/filtering. | `active` (`true`/`false`/`STALE`), `search`, `flag_type` (`boolean`/`experiment`/`multivariant`/`remote_config`), `limit`, `offset` |
| `get_feature_flag` | Full definition + metadata of one flag, including `filters`/targeting. | `flag` (numeric id **or** key) |
| `get_feature_flag_status` | Staleness classification of a flag. | `flag` (numeric id **or** key) |
| `evaluate_flags_for_person` | Explain how every flag evaluates for a person and why. | `distinct_id` |

### Example prompts

- "List the active feature flags in PostHog."
- "Which flags are stale?"
- "Show me the full targeting for the `new-onboarding` flag."
- "Is `beta-dashboard` safe to delete?"
- "How do the flags evaluate for distinct id `user_123`?"

## Scope & limitations

- **Read-only.** No create/update/delete — by design.
- Rate limits are team-wide (480/min, 4800/hr); the server surfaces `429`s clearly.
- Not built in this version (possible future extensions): remote-config payloads,
  activity/version history, SDK `local_evaluation`, and the runtime `/flags`
  evaluator (which uses a different host and the project token instead of the
  personal key).
