"""Tiny client for any OpenAI-compatible chat completions endpoint (stdlib only).

Configure with environment variables:
  LLM_API_KEY   required for real runs
  LLM_BASE_URL  e.g. https://generativelanguage.googleapis.com/v1beta/openai/
  LLM_MODEL     e.g. gemini-3.8-flash
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_MODEL = "gemini-3.8-flash"
# Long SVG replies can take minutes; a short timeout just wastes the attempt.
TIMEOUT = float(os.environ.get("LLM_TIMEOUT") or 240)
# First wait before retrying an overloaded (503) or failed request; it triples after.
RETRY_WAIT = float(os.environ.get("LLM_RETRY_WAIT") or 30)


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL):
        if not api_key:
            raise LLMError("LLM_API_KEY is not set (add it as a repository secret).")
        self.api_key = api_key
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.quota_exhausted = ""  # set once the provider says today's quota is used up

    @classmethod
    def from_env(cls) -> "LLM":
        return cls(
            api_key=os.environ.get("LLM_API_KEY", ""),
            base_url=os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL,
            model=os.environ.get("LLM_MODEL") or DEFAULT_MODEL,
        )

    def chat(self, system: str, user: str, temperature: float = 0.7, retries: int = 3) -> str:
        body = json.dumps({
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }).encode()
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        if self.quota_exhausted:  # no point asking again in this run
            raise LLMError(f"LLM request skipped: {self.quota_exhausted}")
        last: Exception | None = None
        for attempt in range(retries):
            wait = RETRY_WAIT * 3 ** attempt  # 30s, then 90s: overload (503) usually clears in minutes
            started = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                    data = json.load(resp)
                choice = data["choices"][0]
                content = choice["message"].get("content") or ""
                if not content.strip() or choice.get("finish_reason") == "length":
                    raise LLMError(f"empty or cut-off reply (finish_reason={choice.get('finish_reason')!r})")
                return content
            except urllib.error.HTTPError as e:
                body = e.read()
                last = LLMError(f"HTTP {e.code}: {_describe(body)}")
                if e.code == 429 and re.search(rb'"quotaId":\s*"[^"]*PerDay', body):
                    # The daily quota is used up: retrying only wastes time until it resets.
                    self.quota_exhausted = f"daily quota exhausted ({_describe(body)})"
                    break
                if e.code not in (429, 500, 502, 503, 504):
                    break
                wait = max(wait, _retry_after(e, body))
            except (urllib.error.URLError, TimeoutError) as e:
                last = LLMError(f"{type(e).__name__} after {time.monotonic() - started:.0f}s: {e}")
                if time.monotonic() - started >= TIMEOUT * 0.9:
                    break  # timed out: the caller's own retry is the next attempt
            except (LLMError, KeyError, ValueError) as e:
                last = e
                if "finish_reason='length'" in str(e):
                    break  # the same request would be cut off again
            if attempt + 1 < retries:
                time.sleep(wait)
        raise LLMError(f"LLM request failed: {last}")


def _retry_after(e: urllib.error.HTTPError, body: bytes) -> float:
    """Seconds the provider asked us to wait (Retry-After, or Gemini's "retryDelay": "34s"), capped."""
    m = re.search(rb'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', body)
    value = e.headers.get("Retry-After") if e.headers else None
    try:
        seconds = float(m.group(1)) if m else float(value or 0)
    except ValueError:
        seconds = 0
    return min(seconds, 180)


def _describe(body: bytes) -> str:
    """A short, readable error: the provider's message plus any quota it names."""
    text = body.decode("utf-8", "replace")
    message = re.search(r'"message":\s*"((?:[^"\\]|\\.)*)"', text)
    summary = (message.group(1).split("\\n")[0] if message else text)[:160]
    quotas = sorted(set(re.findall(r'"quotaId":\s*"([^"]+)"', text)))
    limits = sorted(set(re.findall(r'"quotaValue":\s*"([^"]+)"', text)))
    if quotas:
        summary += f" [quota: {', '.join(quotas)}{'; limit ' + '/'.join(limits) if limits else ''}]"
    return summary


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (tolerates ``` fences and chatter)."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("no JSON object in reply")
    return json.loads(match.group(0))


def extract_svg(text: str) -> str:
    match = re.search(r"<svg\b.*?</svg>", text, re.S | re.I)
    if not match:
        raise ValueError("no <svg> in reply")
    return match.group(0)
