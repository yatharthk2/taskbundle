#!/usr/bin/env python3
"""OpenAI solver agent — runs INSIDE the task solve box (stdlib only, no openai SDK).

It reads the problem statement ($TASKBUNDLE_PROBLEM) and the repository under /workspace/repo,
asks a cheap OpenAI model for the FULL updated contents of the files it needs to change, and
writes them back to disk. The harness captures the edit via `git diff`, so this script emits no
diff itself. It only ever sees the repo + the problem statement — the hidden pass2pass/fail2pass
buckets are never mounted into the solve box, so it cannot read or game them.

Secrets:  OPENAI_API_KEY is read from the environment at runtime; it is never printed or written.
Model:    OPENAI_MODEL or gpt-4o-mini (cheap), temperature 0 for determinism.
Endpoint: OPENAI_BASE_URL or https://api.openai.com/v1 (override for Azure / a proxy / testing).

On any failure (missing key, HTTP/timeout error, unparseable response) it prints to stderr and
exits non-zero — the harness then classifies the run as no_edits / patch_failed, which is fine.
"""
# ----------------------------- Imports -----------------------------
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# ----------------------------- Config -----------------------------
REPO = Path("/workspace/repo").resolve()
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
HTTP_TIMEOUT = 120          # seconds for the completion call
MAX_FILE_BYTES = 32_000     # skip any single file larger than this
MAX_TOTAL_BYTES = 120_000   # cap the whole prompt payload so it fits the model context
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".venv", "venv",
             "node_modules", ".egg-info", "dist", "build"}


# ----------------------------- Helpers -----------------------------
def _die(msg: str, code: int = 1):
    print(f"openai_agent: {msg}", file=sys.stderr)
    sys.exit(code)


def _http_error_summary(raw: bytes) -> str:
    """A safe one-liner from an OpenAI error body: type/code ONLY, never the human message — which
    echoes a masked fragment of the API key (e.g. 'sk-...****...test') we must not log."""
    try:
        err = json.loads(raw.decode("utf-8", "replace")).get("error") or {}
        return "/".join(p for p in (err.get("type"), err.get("code")) if p) or "error"
    except (json.JSONDecodeError, AttributeError):
        return "error"


def gather_files(root: Path) -> dict:
    """Map relative path -> text for every reasonable source file under root.
    Skips vcs/build dirs, binaries (undecodable), and anything over the size caps."""
    files, total = {}, 0
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in rel.parts):
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if len(data) > MAX_FILE_BYTES:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue                         # binary — skip
        if total + len(data) > MAX_TOTAL_BYTES:
            continue                         # keep the prompt bounded
        files[str(rel)] = text
        total += len(data)
    return files


def build_messages(problem: str, files: dict) -> list:
    """One system + one user message. The model is told to return a JSON object so we can
    parse it deterministically (and it enables the API's JSON response mode)."""
    listing = "\n\n".join(f"### FILE: {path}\n```\n{content}\n```" for path, content in files.items())
    system = (
        "You are an automated software-engineering agent. You are given a problem statement and the "
        "full current contents of a repository's files. Fix the problem by editing the repository. "
        'Respond with ONLY a JSON object of the form {"files": {"<relative/path>": "<full new file '
        'contents>"}} listing every file you changed, each with its COMPLETE new contents (never a '
        "diff or a fragment). Include only files you actually modified; change as little as possible."
    )
    user = f"PROBLEM STATEMENT:\n{problem}\n\nREPOSITORY FILES:\n{listing}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def call_openai(messages: list) -> str:
    """POST the chat completion and return the model's message content (a JSON string)."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        _die("OPENAI_API_KEY is not set in the environment", 2)
    model = os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL
    url = (os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}",            # never logged
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _die(f"OpenAI HTTP {e.code} ({_http_error_summary(e.read())})", 3)   # type/code only — no key echo
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        _die(f"OpenAI request failed: {e}", 3)
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        _die(f"unexpected OpenAI response shape: {e}", 4)


def apply_files(content: str, root: Path) -> list:
    """Parse the model's JSON and write each file's full new contents back under root.
    Refuses any path that escapes the repo (path-traversal guard)."""
    root = Path(root).resolve()          # resolve once so the guard compares like-for-like (symlinked tmp dirs)
    try:
        obj = json.loads(content)
    except json.JSONDecodeError as e:
        _die(f"could not parse model output as JSON: {e}", 4)
    files = obj.get("files") if isinstance(obj, dict) else None
    if not isinstance(files, dict) or not files:
        _die("model returned no files to write", 5)
    written = []
    for rel, new_content in files.items():
        if not isinstance(new_content, str):
            continue
        dest = (root / rel).resolve()
        if dest != root and root not in dest.parents:
            _die(f"refusing to write outside the repo: {rel}", 6)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(new_content)
        written.append(str(rel))
    if not written:
        _die("model output contained no writable file contents", 5)
    return written


# ----------------------------- Entry point -----------------------------
def main():
    problem_path = os.environ.get("TASKBUNDLE_PROBLEM")
    problem = Path(problem_path).read_text() if problem_path and Path(problem_path).is_file() else ""
    if not problem.strip():
        _die("no problem statement ($TASKBUNDLE_PROBLEM) provided", 2)
    files = gather_files(REPO)
    if not files:
        _die(f"no source files found under {REPO}", 2)
    model = os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL
    print(f"openai_agent: model={model}, {len(files)} repo file(s) in context", file=sys.stderr)
    content = call_openai(build_messages(problem, files))
    written = apply_files(content, REPO)
    print(f"openai_agent: wrote {len(written)} file(s): {', '.join(written)}", file=sys.stderr)


if __name__ == "__main__":
    main()
