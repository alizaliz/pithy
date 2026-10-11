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


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL):
        if not api_key:
            raise LLMError("LLM_API_KEY is not set (add it as a repository secret).")
        self.api_key = api_key
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model

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
        last: Exception | None = None
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.load(resp)
                return data["choices"][0]["message"]["content"] or ""
            except urllib.error.HTTPError as e:
                last = LLMError(f"HTTP {e.code}: {e.read()[:300]!r}")
                if e.code not in (429, 500, 502, 503, 504):
                    break
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as e:
                last = e
            time.sleep(5 * (attempt + 1))
        raise LLMError(f"LLM request failed: {last}")


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
