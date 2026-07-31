"""Single source of truth for Claude Code (Anthropic subscription) credential facts.

The provider key, its accepted aliases, the default model, the OAuth endpoints and
the token filename are consumed by two places: the config schema validator and the
CLI login flow. Keeping them here prevents the schema from rejecting a config the
CLI just wrote (or from silently ignoring a newly added alias).
"""

# Canonical provider key used by schema fields, registry lookups and runtime resolution.
CLAUDE_CODE_PROVIDER_KEY = "anthropic_claude_code"

# Every spelling a user (or an older config) may write for the same provider.
CLAUDE_CODE_ALIASES: tuple[str, ...] = (
    "anthropic_claude_code",
    "anthropic-claude-code",
    "claude_code",
    "claude-code",
    "claudecode",
)

CLAUDE_CODE_DEFAULT_MODEL = "anthropic-claude-code/claude-opus-4-6"

# Mirrors nanobot/providers/oauth_store.py::_TOKEN_FILENAME (asserted by tests).
CLAUDE_CODE_TOKEN_FILENAME = "claude-code.json"

CLAUDE_CODE_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
CLAUDE_CODE_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLAUDE_CODE_REDIRECT_URI = "https://platform.claude.com/oauth/code/callback"
CLAUDE_CODE_SCOPE = "user:inference user:profile user:sessions:claude_code user:mcp_servers"

_NORMALIZED_ALIASES = frozenset(alias.replace("-", "_").lower() for alias in CLAUDE_CODE_ALIASES)


def normalize_claude_provider_key(name: str | None) -> str | None:
    """Return the canonical Claude Code provider key, or None for other providers."""
    if not name:
        return None
    key = name.strip().lower().replace("-", "_")
    return CLAUDE_CODE_PROVIDER_KEY if key in _NORMALIZED_ALIASES else None


def claude_code_oauth_provider_kwargs(client_id: str) -> dict[str, str]:
    """Keyword arguments for oauth_cli_kit's OAuthProviderConfig."""
    return {
        "client_id": client_id,
        "authorize_url": CLAUDE_CODE_AUTHORIZE_URL,
        "token_url": CLAUDE_CODE_TOKEN_URL,
        "redirect_uri": CLAUDE_CODE_REDIRECT_URI,
        "scope": CLAUDE_CODE_SCOPE,
        "token_filename": CLAUDE_CODE_TOKEN_FILENAME,
    }
