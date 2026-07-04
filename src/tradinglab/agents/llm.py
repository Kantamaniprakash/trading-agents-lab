"""Anthropic LLM client with disk caching, usage tracking, and retries.

The client is constructed lazily so importing this module (or building an
``LLMClient``) never requires an API key; the key is only needed on the
first actual ``chat()`` call.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

import anthropic

from tradinglab.config import LLM_CACHE_DIR, AgentConfig

# Extra client-side retries for transient connection failures, on top of the
# SDK's own retry logic (max_retries=4 covers 429/5xx).
_CONNECTION_RETRIES = 2
_CONNECTION_RETRY_SLEEP_S = 5.0


class LLMClient:
    """Cached, usage-tracked wrapper around the Anthropic Messages API.

    Responses are cached on disk keyed by (model, system, user) so repeated
    backtest runs are deterministic and free. Never passes ``temperature``
    (removed on claude-opus-4-8 — returns 400).
    """

    def __init__(self, cfg: AgentConfig, cache_dir: Path | None = None):
        self.cfg = cfg
        self.cache_dir = Path(cache_dir) if cache_dir is not None else LLM_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client: anthropic.Anthropic | None = None
        self._usage: dict[str, dict[str, int]] = {}

    def _get_client(self) -> anthropic.Anthropic:
        """Construct the Anthropic client on first use; RuntimeError if auth fails."""
        if self._client is None:
            try:
                self._client = anthropic.Anthropic(max_retries=4)
            except Exception as exc:  # missing key / unresolvable auth
                raise RuntimeError(
                    "Could not construct the Anthropic client. Set the "
                    "ANTHROPIC_API_KEY environment variable (or configure "
                    "ambient auth) before running agent commands."
                ) from exc
        return self._client

    def _usage_bucket(self, model: str) -> dict[str, int]:
        if model not in self._usage:
            self._usage[model] = {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_hits": 0,
            }
        return self._usage[model]

    def _cache_path(self, model: str, system: str, user: str) -> Path:
        key = hashlib.sha256(f"{model}|{system}|{user}".encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.json"

    def chat(self, system: str, user: str, model: str | None = None,
             max_tokens: int | None = None) -> str:
        """Send one system+user exchange and return the response text.

        Defaults: ``model`` = cfg.deep_model, ``max_tokens`` = cfg.max_tokens.
        Serves from the disk cache when an identical request was seen before.
        """
        model = model or self.cfg.deep_model
        max_tokens = max_tokens if max_tokens is not None else self.cfg.max_tokens
        bucket = self._usage_bucket(model)

        cache_path = self._cache_path(model, system, user)
        if cache_path.exists():
            try:
                with open(cache_path, encoding="utf-8") as fh:
                    entry = json.load(fh)
                bucket["cache_hits"] += 1
                return entry["response"]
            except (json.JSONDecodeError, KeyError, OSError):
                pass  # corrupt cache file: fall through to a live call

        client = self._get_client()
        last_exc: Exception | None = None
        for attempt in range(_CONNECTION_RETRIES + 1):
            try:
                resp = client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
                break
            except TypeError as exc:
                # The SDK raises TypeError at request time when no credentials
                # can be resolved (constructing the client succeeds without one).
                if "authentication" in str(exc).lower():
                    raise RuntimeError(
                        "No Anthropic API credentials found. Set the "
                        "ANTHROPIC_API_KEY environment variable (or configure "
                        "ambient auth) before running agent commands."
                    ) from exc
                raise
            except anthropic.APIConnectionError as exc:
                last_exc = exc
                if attempt < _CONNECTION_RETRIES:
                    time.sleep(_CONNECTION_RETRY_SLEEP_S)
        else:
            raise RuntimeError(
                f"Anthropic API connection failed after "
                f"{_CONNECTION_RETRIES + 1} attempts."
            ) from last_exc

        text = "".join(b.text for b in resp.content if b.type == "text")

        bucket["calls"] += 1
        bucket["input_tokens"] += resp.usage.input_tokens
        bucket["output_tokens"] += resp.usage.output_tokens

        entry = {
            "model": model,
            "response": text,
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
        }
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(entry, fh, ensure_ascii=False, indent=2)

        return text

    def quick(self, system: str, user: str) -> str:
        """Chat with the cheap/fast model (cfg.quick_model)."""
        return self.chat(system, user, model=self.cfg.quick_model)

    def deep(self, system: str, user: str) -> str:
        """Chat with the reasoning-heavy model (cfg.deep_model)."""
        return self.chat(system, user, model=self.cfg.deep_model)

    @property
    def usage(self) -> dict:
        """Per-model usage: {model: {calls, input_tokens, output_tokens, cache_hits}}."""
        return {model: dict(stats) for model, stats in self._usage.items()}


def _balanced_object(text: str, start: int) -> str | None:
    """Return the balanced ``{...}`` substring beginning at ``start``, or None.

    String-aware: braces inside JSON strings (including escaped quotes) do
    not affect the depth count.
    """
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def extract_json(text: str) -> dict:
    """Extract the first parseable balanced ``{...}`` region from ``text``.

    Tolerates markdown code fences (```json ... ```) — including a fence on
    the same line as the payload — and surrounding prose. Raises ValueError
    if no parseable JSON object is found.
    """
    # Strip fence markers themselves (not whole lines) so JSON sharing a
    # line with the fence survives, e.g. '```json {"a": 1} ```'.
    cleaned = re.sub(r"```[A-Za-z0-9_+-]*", " ", text)

    start = cleaned.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in text: {text[:200]!r}")

    # Try each '{' in order: skip balanced regions that fail json.loads
    # (e.g. prose braces before the real payload) and unbalanced openers.
    while start != -1:
        candidate = _balanced_object(cleaned, start)
        if candidate is not None:
            try:
                obj = json.loads(candidate)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(obj, dict):
                    return obj
        start = cleaned.find("{", start + 1)

    raise ValueError(f"No parseable JSON object found in text: {text[:200]!r}")
