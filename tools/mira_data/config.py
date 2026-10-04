"""Local config loader for the data substrate (stdlib only).

Resolution order (first hit wins):

1. environment variables
2. an explicit file named by ``MIRA_DATA_CONFIG``
3. ``private/mira-data.env`` (gitignored — the standard user location)
4. ``~/.config/mira/mira-data.env``

The file is a simple ``KEY=value`` env format (``#`` comments allowed), so no
third-party parser is needed. User-specific contact + API keys live in
gitignored ``private/`` per the repo's private-state boundary — never tracked.

SEC's access policy requires a contactable User-Agent. Official-data adapters
therefore gate on :func:`is_contact_configured`; users opt in by setting their
real contact, exactly as ``templates/mira-data-config.example`` documents.
"""

from __future__ import annotations

import os
from typing import Callable, Optional


class ConfigGap(RuntimeError):
    """A configuration gap that blocks a read (currently: a missing API key)."""


# What ``require_api_key`` actually raises. ``net`` registers its ``FetchError`` here at
# import; until then the base class stands in.
#
# The indirection is deliberate, and was arrived at by getting it wrong twice. ``net``
# imports ``config``, so ``config`` cannot import ``net`` to name ``FetchError``. What
# matters is the *raised* type: every adapter catches ``net.FetchError``, so the gap must
# be an instance of that class. Raising the PARENT (``ConfigGap``) does not work - an
# ``except FetchError`` does not catch its own base - which was verified rather than
# assumed, after the MRO looked correct and the behaviour still surprised.
_RAISE: Callable[[str], BaseException] = ConfigGap


def bind_error_factory(factory: Callable[[str], BaseException]) -> None:
    """Called by ``net`` at import so config gaps raise the adapter-catchable type."""
    global _RAISE
    _RAISE = factory

CONFIG_ENV = "MIRA_DATA_CONFIG"
DEFAULT_PATHS = [
    "private/mira-data.env",
    os.path.expanduser("~/.config/mira/mira-data.env"),
]
# Shipped placeholder: works for sources that don't require identification, but
# is_contact_configured() treats it as "not configured" so SEC stays gated.
PLACEHOLDER_UA = "Mira-market-research-agents contact@example.com"

_file_cache: dict | None = None


def _candidate_paths() -> list[str]:
    paths = []
    explicit = os.environ.get(CONFIG_ENV)
    if explicit:
        paths.append(explicit)
    paths.extend(DEFAULT_PATHS)
    return paths


def _load_file(path: str) -> dict:
    data: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key:
                    data[key] = val
    except OSError:
        pass
    return data


def _file_config() -> dict:
    global _file_cache
    if _file_cache is None:
        merged: dict[str, str] = {}
        # later paths are lower priority: load them first, let earlier overwrite.
        for path in reversed(_candidate_paths()):
            merged.update(_load_file(path))
        _file_cache = merged
    return _file_cache


def reset_cache() -> None:
    """Drop the cached file config (used by tests / after writing the file)."""
    global _file_cache
    _file_cache = None


def get(key: str, default: str | None = None) -> str | None:
    """Resolve ``key`` from env first, then the config file."""
    if key in os.environ:
        return os.environ[key]
    return _file_config().get(key, default)


def contact_ua() -> tuple[str, bool]:
    """Return ``(user_agent, configured)``.

    ``configured`` is False only when we fall back to the shipped placeholder.
    """
    ua = get("MIRA_HTTP_UA")
    if ua:
        return ua, True
    email = get("MIRA_CONTACT_EMAIL")
    if email:
        name = get("MIRA_CONTACT_NAME", "Mira-market-research-agents")
        return f"{name} {email}", True
    return PLACEHOLDER_UA, False


def is_contact_configured() -> bool:
    return contact_ua()[1]


def api_key(name: str) -> Optional[str]:
    """Resolved value of a keyed source, or ``None`` when unset.

    A blank value and a commented-out line are both "not configured" — the shipped
    ``private/mira-data.env`` template keeps ``FRED_API_KEY=`` / ``BEA_API_KEY=`` as
    empty placeholders, and treating an empty string as a key would send a request the
    provider rejects for the wrong reason.
    """
    value = (get(name) or "").strip()
    return value or None


def require_api_key(name: str, *, label: str) -> str:
    """Return a configured key or raise a routable setup gap naming the exact step.

    Same posture the SEC contact gate takes: the substrate refuses to call an official
    endpoint without the credential it requires, and says how to fix it instead of
    degrading to a silent empty read.
    """
    value = api_key(name)
    if value:
        return value
    raise _RAISE(
        f"{label}_key_gap: {name} is not configured, so {label} cannot be read. "
        f"Add `{name}=<your key>` to private/mira-data.env (gitignored) - see "
        "templates/mira-data-config.example. Both providers issue a free key.")


def config_hint() -> str:
    """One-line instruction for configuring a contact."""
    return (
        "Set your contact in private/mira-data.env "
        "(MIRA_CONTACT_EMAIL=you@domain.com) - see templates/mira-data-config.example."
    )
