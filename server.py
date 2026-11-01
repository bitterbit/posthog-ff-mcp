# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp==1.30.0", "httpx==0.28.1"]
# ///
"""Read-only MCP server wrapping the PostHog REST API for feature flags.

Exposes a small set of read-only tools so Claude can inspect feature-flag state
and metadata. It never creates, updates, or deletes anything.

Configuration is entirely via environment variables (no defaults):
  POSTHOG_HOST              full base URL of the app host, e.g. https://eu.posthog.com
  POSTHOG_PROJECT_ID        numeric project id, e.g. 13105
  POSTHOG_PERSONAL_API_KEY  personal API key scoped 'feature_flag:read'
"""

from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "Set POSTHOG_HOST, POSTHOG_PROJECT_ID, and POSTHOG_PERSONAL_API_KEY."
        )
    return value


# Validated at import time so a misconfigured server fails fast with a clear message
# rather than erroring on every tool call.
POSTHOG_HOST = _require_env("POSTHOG_HOST").rstrip("/")
POSTHOG_PROJECT_ID = _require_env("POSTHOG_PROJECT_ID")
POSTHOG_PERSONAL_API_KEY = _require_env("POSTHOG_PERSONAL_API_KEY")

# One reused client. The API key lives only in the default header — never logged.
_client = httpx.Client(
    base_url=POSTHOG_HOST,
    headers={
        "Authorization": f"Bearer {POSTHOG_PERSONAL_API_KEY}",
        "Accept": "application/json",
    },
    timeout=15.0,
)

mcp = FastMCP("posthog-feature-flags")


def _ff_path(suffix: str = "") -> str:
    """Build a project-scoped feature-flags path (trailing slashes matter to PostHog)."""
    return f"/api/projects/{POSTHOG_PROJECT_ID}/feature_flags/{suffix}"


def _get(path: str, params: dict | None = None) -> dict:
    """GET a PostHog endpoint, mapping common failures to actionable messages."""
    try:
        resp = _client.get(path, params=params)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Request to PostHog failed: {exc}") from exc

    if resp.status_code == 401:
        raise RuntimeError(
            "PostHog returned 401 Unauthorized — check POSTHOG_PERSONAL_API_KEY."
        )
    if resp.status_code == 403:
        raise RuntimeError(
            "PostHog returned 403 Forbidden — the key must be scoped to this project "
            "and have the 'feature_flag:read' scope."
        )
    if resp.status_code == 404:
        raise RuntimeError(
            f"PostHog returned 404 Not Found for {path} — check the flag id/key and "
            "that POSTHOG_PROJECT_ID / POSTHOG_HOST point at the right project."
        )
    if resp.status_code == 429:
        raise RuntimeError(
            "PostHog rate limit hit (429). Limits are team-wide and shared; "
            "wait a moment and retry."
        )
    resp.raise_for_status()
    return resp.json()


def _flag_type(flag: dict) -> str:
    """Derive a human-friendly flag type from its shape."""
    if flag.get("is_remote_configuration"):
        return "remote_config"
    if flag.get("experiment_set"):
        return "experiment"
    if (flag.get("filters") or {}).get("multivariate"):
        return "multivariate"
    return "boolean"


def _rollout_summary(flag: dict):
    """Compact rollout view: variant splits for multivariate, else group rollout %s."""
    filters = flag.get("filters") or {}
    multivariate = filters.get("multivariate")
    if multivariate and multivariate.get("variants"):
        return {
            v.get("key"): v.get("rollout_percentage")
            for v in multivariate["variants"]
        }
    rollouts = [g.get("rollout_percentage") for g in (filters.get("groups") or [])]
    if len(rollouts) == 1:
        return rollouts[0]
    return rollouts


def _summarize(flag: dict) -> dict:
    """Small, list-friendly view of a flag — full detail lives in get_feature_flag."""
    created_by = flag.get("created_by") or {}
    return {
        "id": flag.get("id"),
        "key": flag.get("key"),
        "name": flag.get("name"),
        "active": flag.get("active"),
        "type": _flag_type(flag),
        "rollout": _rollout_summary(flag),
        "tags": flag.get("tags") or [],
        "created_by": created_by.get("email"),
        "created_at": flag.get("created_at"),
        "status": flag.get("status"),
    }


def _resolve_flag_id(flag: str) -> int:
    """Accept either a numeric flag id or a flag key; return the numeric id."""
    flag = str(flag).strip()
    if flag.isdigit():
        return int(flag)
    data = _get(_ff_path(), params={"search": flag, "limit": 100})
    for candidate in data.get("results", []):
        if candidate.get("key") == flag:
            return candidate["id"]
    raise RuntimeError(f"No feature flag found with key '{flag}'.")


@mcp.tool()
def list_feature_flags(
    active: str | None = None,
    search: str | None = None,
    flag_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List feature flags in the configured PostHog project (read-only).

    Returns compact summaries; use get_feature_flag for a flag's full definition.

    Args:
        active: Filter by state — "true", "false", or "STALE" (staleness-detected).
        search: Substring match against flag key/name.
        flag_type: Filter by type — "boolean", "experiment", "multivariant",
            or "remote_config".
        limit: Max flags to return (default 100).
        offset: Pagination offset; pass the returned next_offset to page forward.

    Returns:
        {count, returned, next_offset, flags: [ {id, key, name, active, type,
        rollout, tags, created_by, created_at, status} ]}
    """
    params: dict = {"limit": limit, "offset": offset}
    if active is not None:
        params["active"] = active
    if search:
        params["search"] = search
    if flag_type:
        params["type"] = flag_type

    data = _get(_ff_path(), params=params)
    results = data.get("results", [])
    next_offset = offset + len(results) if data.get("next") else None
    return {
        "count": data.get("count"),
        "returned": len(results),
        "next_offset": next_offset,
        "flags": [_summarize(f) for f in results],
    }


@mcp.tool()
def get_feature_flag(flag: str) -> dict:
    """Get the full definition and metadata of a single feature flag (read-only).

    Args:
        flag: The flag's numeric id or its human-readable key.

    Returns:
        The complete flag object, including its `filters` (release conditions,
        multivariate variants, payloads), targeting, and audit metadata.
    """
    flag_id = _resolve_flag_id(flag)
    return _get(_ff_path(f"{flag_id}/"))


@mcp.tool()
def get_feature_flag_status(flag: str) -> dict:
    """Get the staleness status of a feature flag (read-only).

    PostHog marks a flag stale when it hasn't been evaluated in 30+ days, or has
    been fully rolled out for 30+ days with no property filters. Disabled flags are
    not considered stale.

    Args:
        flag: The flag's numeric id or its human-readable key.

    Returns:
        The flag's status payload (e.g. its staleness classification and reason).
    """
    flag_id = _resolve_flag_id(flag)
    return _get(_ff_path(f"{flag_id}/status/"))


@mcp.tool()
def evaluate_flags_for_person(distinct_id: str) -> dict:
    """Explain how every feature flag evaluates for a given person (read-only).

    Uses PostHog's evaluation_reasons endpoint to report, per flag, the value the
    person would receive and why (which condition matched or why none did).

    Args:
        distinct_id: The person's distinct id.

    Returns:
        Per-flag evaluation results with reasons.
    """
    return _get(_ff_path("evaluation_reasons/"), params={"distinct_id": distinct_id})


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
