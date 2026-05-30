"""Runtime null-guard for the OpenAI SDK Responses parser (Codex backend).

The ChatGPT Codex backend (``chatgpt.com/backend-api/codex``) intermittently
returns ``response.output = None`` instead of ``[]`` in ``response.completed``
SSE events (observed in production ~2026-05-28). ``openai==2.32.0``'s
``lib/_parsing/_responses.py::parse_response`` iterates ``response.output``
with no null guard::

    for output in response.output:   # TypeError when output is None

so the SDK raises ``TypeError: 'NoneType' object is not iterable`` *inside*
``stream.get_final_response()`` — before any Hermes adapter / backfill code
runs. Classified non-retryable, it kills every Codex turn (interactive +
cron). This is upstream bug NousResearch/hermes-agent#33578 (and ~10 dupes:
#33502 #33298 #33237 #33204 #33011 #32956 #32908 #32903 #32894 #32883),
unfixed as of our pinned fork.

This module wraps the SDK's ``parse_response`` to coalesce ``response.output``
to ``[]`` before delegating, and rebinds the patched callable in every SDK
module that imported it by name (the streaming module is the actual crash
site). Idempotent and defensive: if the SDK layout changes it logs and
no-ops rather than raising at import.

Loaded for side effect (``apply()`` runs at import) from the top of
``agent/transports/codex.py`` so the patch is in place at gateway startup
before any Codex request. Lives in our fork (git-tracked) so it survives
ansible-pull ``reset --hard`` AND ``pip install openai`` reinstalls — unlike
a site-packages ``sed``.

When upstream merges a real fix, delete this module and the import in
``agent/transports/codex.py``.
"""

from __future__ import annotations

import importlib
import logging
import sys

logger = logging.getLogger(__name__)

_PATCH_FLAG = "_hermes_codex_nullguard"

# Modules that bind ``parse_response`` by name. The streaming module is the
# live crash path (get_final_response → parse_response); the others are
# patched for completeness so the non-streaming Responses path is covered too.
_REBIND_TARGETS = (
    "openai.lib._parsing._responses",
    "openai.lib._parsing",
    "openai.lib.streaming.responses._responses",
    "openai.resources.responses.responses",
)


def _coalesce_output(response: object) -> None:
    """If ``response.output`` is None, set it to ``[]`` in place (best effort)."""
    if response is None:
        return
    if getattr(response, "output", "__missing__") is not None:
        return
    logger.warning(
        "codex_sdk_nullguard: response.output is None (Codex null-output bug "
        "hermes-agent#33578) — coalescing to [] before SDK parse"
    )
    try:
        response.output = []          # pydantic v2 allows attribute assignment
    except Exception:
        try:
            response.__dict__["output"] = []   # bulletproof fallback
        except Exception:
            logger.error("codex_sdk_nullguard: could not coalesce response.output")


def apply() -> bool:
    """Patch and rebind ``parse_response``. Returns True if active (or already)."""
    try:
        from openai.lib._parsing import _responses as _resp_mod
    except Exception as exc:  # SDK missing / renamed — nothing to do
        logger.warning("codex_sdk_nullguard: SDK parser import failed (%s); skipping", exc)
        return False

    orig = getattr(_resp_mod, "parse_response", None)
    if orig is None:
        logger.warning("codex_sdk_nullguard: parse_response not found; skipping")
        return False
    if getattr(orig, _PATCH_FLAG, False):
        return True  # already patched in this process

    def parse_response(*args, **kwargs):
        # parse_response signature is keyword-only: (*, text_format,
        # input_tools, response). Guard both kw and positional just in case.
        resp = kwargs.get("response")
        if resp is None and args:
            resp = args[-1]
        _coalesce_output(resp)
        return orig(*args, **kwargs)

    setattr(parse_response, _PATCH_FLAG, True)

    patched = []
    for mod_name in _REBIND_TARGETS:
        mod = sys.modules.get(mod_name)
        if mod is None:
            try:
                mod = importlib.import_module(mod_name)
            except Exception:
                continue
        if getattr(mod, "parse_response", None) is orig:
            setattr(mod, "parse_response", parse_response)
            patched.append(mod_name)

    logger.info(
        "codex_sdk_nullguard: parse_response patched in %d module(s): %s",
        len(patched), ", ".join(patched) or "(none — already rebound?)",
    )
    return True


apply()
