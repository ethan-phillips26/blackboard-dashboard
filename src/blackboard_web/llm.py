"""Ask a model for the few things that are genuinely open-ended, and cache them.

The dashboard needs a model for two jobs: reading a syllabus for its grade
weighting, and sorting the gradebook's rows into that weighting. Every number on
screen is arithmetic done locally in `grades.py`, so a weaker model costs
accuracy in how the weighting is inferred, never in the percentages.

Two backends serve those jobs, chosen with BB_LLM:

    claude   (default)  the `claude` CLI, run non-interactively
    ollama              a model served locally by Ollama, over HTTP

Both are asked for JSON against the same schema and both return parsed data, so
callers cannot tell them apart. Results are keyed by a hash of their inputs, so a
syllabus is read once and reused until the document or the gradebook it is being
matched against actually changes.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Any

import httpx

from .cache import Cache, digest

# A local 8B model is an order of magnitude slower than the API, and the syllabus
# prompt is the longest one we send.
CLAUDE_TIMEOUT = 240
OLLAMA_TIMEOUT = 600

OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_MODEL = "qwen3:8b"

# Ollama silently truncates anything past the context window rather than
# erroring, and a syllabus that loses its grade table produces a confidently
# empty answer. 32k covers the longest syllabus we send with room for the reply.
OLLAMA_NUM_CTX = 32768


class LLMError(RuntimeError):
    pass


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else default


def backend() -> str:
    """Which backend is configured. Read per call so .env can change under us."""
    return (os.environ.get("BB_LLM") or "claude").strip().lower()


def model() -> str:
    """The model name to report, for the health endpoint."""
    if backend() == "ollama":
        return (os.environ.get("BB_OLLAMA_MODEL") or OLLAMA_MODEL).strip()
    return "claude"


def _ollama_url() -> str:
    return (os.environ.get("BB_OLLAMA_URL") or OLLAMA_URL).rstrip("/")


def _timeout() -> int:
    return _env_int("BB_LLM_TIMEOUT",
                    OLLAMA_TIMEOUT if backend() == "ollama" else CLAUDE_TIMEOUT)


def max_document_chars() -> int:
    """How much of a syllabus is worth sending.

    The weighting table is almost always in the first few pages, and on a local
    model every extra page is both context pressure and wall-clock time.
    """
    return _env_int("BB_LLM_DOC_CHARS", 24000 if backend() == "ollama" else 60000)


def available() -> bool:
    """Whether the configured backend can actually be reached right now."""
    if backend() == "ollama":
        try:
            return httpx.get(f"{_ollama_url()}/api/tags", timeout=2.0).status_code == 200
        except httpx.HTTPError:
            return False
    from shutil import which
    return which("claude") is not None


# ------------------------------------------------------------------- ollama


def _parse_json(text: str) -> Any:
    """Parse the model's answer, tolerating anything it wrapped around the JSON."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except ValueError:
            pass
    raise LLMError(f"the model did not return JSON: {text[:300]}")


def _run_ollama(instruction: str, data: str, schema: dict[str, Any] | None,
                timeout: int) -> Any:
    url = f"{_ollama_url()}/api/chat"
    name = model()
    body: dict[str, Any] = {
        "model": name,
        "messages": [{"role": "system", "content": instruction},
                     {"role": "user", "content": data}],
        "stream": False,
        "options": {
            "num_ctx": _env_int("BB_OLLAMA_CTX", OLLAMA_NUM_CTX),
            "temperature": 0,
        },
    }
    if schema:
        # Ollama constrains generation to the schema, so these are the same
        # schemas the CLI backend is given, passed through unchanged.
        body["format"] = schema

    def post(payload: dict[str, Any]) -> httpx.Response:
        try:
            return httpx.post(url, json=payload, timeout=timeout)
        except httpx.TimeoutException as e:
            raise LLMError(f"{name} timed out after {timeout}s") from e
        except httpx.HTTPError as e:
            raise LLMError(
                f"could not reach Ollama at {_ollama_url()}: {e}. "
                "Is `ollama serve` running?"
            ) from e

    # A reasoning model spends its context thinking before it answers, and the
    # answer here is a fixed JSON shape that gains nothing from it. Models with
    # no thinking mode reject the flag outright, so it comes back off for them.
    resp = post({**body, "think": False})
    if resp.status_code == 400 and "think" in resp.text.lower():
        resp = post(body)

    if resp.status_code == 404:
        raise LLMError(f"Ollama has no model named {name!r}. Run: ollama pull {name}")
    if resp.status_code != 200:
        raise LLMError(f"Ollama returned {resp.status_code}: {resp.text[:300]}")
    try:
        content = resp.json()["message"]["content"]
    except (ValueError, KeyError, TypeError) as e:
        raise LLMError(f"Ollama returned an unexpected body: {resp.text[:300]}") from e
    if not schema:
        return content
    if not (content or "").strip():
        raise LLMError(
            f"{name} returned nothing. This usually means the prompt overflowed "
            f"the context window — raise BB_OLLAMA_CTX or lower BB_LLM_DOC_CHARS."
        )
    return _parse_json(content)


# ------------------------------------------------------------------- claude


def _claude_command(schema: dict[str, Any] | None) -> list[str]:
    cmd = ["claude", "-p"]
    # With an API key present, --bare starts faster and ignores whatever hooks,
    # MCP servers or CLAUDE.md happen to be in the working directory. Without
    # one, the plain form is what picks up an existing Claude Code login.
    if os.environ.get("ANTHROPIC_API_KEY"):
        cmd.insert(1, "--bare")
    cmd += ["--output-format", "json"]
    if schema:
        cmd += ["--json-schema", json.dumps(schema)]
    return cmd


def _run_claude(instruction: str, data: str, schema: dict[str, Any] | None,
                timeout: int) -> Any:
    from shutil import which
    if which("claude") is None:
        raise LLMError(
            "The `claude` CLI was not found on PATH. Install it, or set "
            "BB_LLM=ollama to use a local model instead."
        )
    # Run somewhere neutral so a project's own config cannot alter the result.
    with tempfile.TemporaryDirectory() as cwd:
        try:
            proc = subprocess.run(
                _claude_command(schema), input=data, capture_output=True,
                text=True, timeout=timeout, cwd=cwd,
            )
        except subprocess.TimeoutExpired as e:
            raise LLMError(f"claude timed out after {timeout}s") from e
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:400]
        raise LLMError(f"claude exited {proc.returncode}: {detail}")
    try:
        payload = json.loads(proc.stdout)
    except ValueError as e:
        raise LLMError(f"claude returned non-JSON: {proc.stdout[:300]}") from e
    if payload.get("is_error"):
        raise LLMError(f"claude reported an error: {str(payload.get('result'))[:300]}")
    if schema:
        out = payload.get("structured_output")
        if out is None:
            raise LLMError("claude returned no structured_output for the schema")
        return out
    return payload.get("result", "")


# -------------------------------------------------------------------- shared


def run(instruction: str, data: str, schema: dict[str, Any] | None = None,
        timeout: int | None = None) -> Any:
    """Send `data` with `instruction` as the prompt; return the result.

    With a schema, the parsed object comes back; without one, the response text.
    """
    timeout = timeout or _timeout()
    which = backend()
    if which == "ollama":
        return _run_ollama(instruction, data, schema, timeout)
    if which != "claude":
        raise LLMError(f"BB_LLM is {which!r}; expected 'claude' or 'ollama'.")
    return _run_claude(instruction, data, schema, timeout)


def cached(cache: Cache, key: str, instruction: str, data: str,
           schema: dict[str, Any] | None = None, force: bool = False) -> dict[str, Any]:
    """`run`, but skipped entirely when the inputs are unchanged.

    The backend is part of the fingerprint: switching models should produce a new
    answer rather than serve one the other model wrote.
    """
    fingerprint = digest([instruction, data, schema, backend(), model()])
    existing = cache.read(key)
    if not force and existing and existing.get("inputs") == fingerprint:
        return {"data": existing.get("data"), "cached": True,
                "generated_at": existing.get("fetched_at")}
    result = run(instruction, data, schema)
    entry = cache.write(key, result, inputs=fingerprint)
    return {"data": result, "cached": False, "generated_at": entry["fetched_at"]}
