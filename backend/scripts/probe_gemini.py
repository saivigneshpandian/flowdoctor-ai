"""Sends a short generateContent request to each model and reports which respond, and how fast.

Run from backend/: .venv/Scripts/python scripts/probe_gemini.py
"""

import sys
import time
from pathlib import Path

import httpx

MODELS = [
    "gemini-flash-latest",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.8-flash",
]
BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT = 60.0
PROMPT = "Reply with the single word OK."
# Default thinking, then the lowest settings the API accepts.
THINKING = {
    "default": None,
    "level=minimal": {"thinkingLevel": "minimal"},
    "budget=0": {"thinkingBudget": 0},
}


def read_key() -> str:
    env_file = Path(__file__).resolve().parent.parent / ".env"
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("GEMINI_API_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("GEMINI_API_KEY is not in backend/.env")


def probe(client: httpx.Client, model: str, thinking: dict | None) -> tuple[str, float, str]:
    body: dict = {"contents": [{"parts": [{"text": PROMPT}]}]}
    if thinking:
        body["generationConfig"] = {"thinkingConfig": thinking}
    started = time.monotonic()
    try:
        response = client.post(f"/models/{model}:generateContent", json=body)
    except httpx.HTTPError as exc:
        return type(exc).__name__, time.monotonic() - started, ""
    elapsed = time.monotonic() - started
    data = response.json() if response.content else {}
    if response.is_error:
        error = data.get("error", {})
        detail = f"{error.get('status')}: {str(error.get('message'))[:90]}"
        return f"HTTP {response.status_code}", elapsed, detail
    usage = data.get("usageMetadata", {})
    version = data.get("modelVersion", "?")
    return "OK", elapsed, f"served by {version}, thought tokens {usage.get('thoughtsTokenCount', 0)}"


def main() -> None:
    headers = {"x-goog-api-key": read_key()}
    with httpx.Client(base_url=BASE_URL, headers=headers, timeout=TIMEOUT) as client:
        for model in MODELS:
            for label, thinking in THINKING.items():
                status, elapsed, detail = probe(client, model, thinking)
                print(f"{model:22} {label:14} {status:10} {elapsed:6.2f}s  {detail}", flush=True)


if __name__ == "__main__":
    main()
