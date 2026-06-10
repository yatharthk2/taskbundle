"""openai_agent (the stdlib LLM solver): file gathering, prompt, parse/write-back, path-traversal
guard, and the HTTP transport (mocked) — runtime-free, no real API call and no container."""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# load the standalone agent script (it lives outside the package, under cli/solvers/)
_AGENT_PATH = Path(__file__).resolve().parents[1] / "solvers" / "openai_agent.py"
_spec = importlib.util.spec_from_file_location("openai_agent", _AGENT_PATH)
agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent)


class _Resp:
    """Minimal context-manager stand-in for urlopen()'s return value."""
    def __init__(self, data): self._data = data
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return self._data


class TestGather(unittest.TestCase):
    def test_skips_vcs_binary_and_oversize(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "calc").mkdir()
            (root / "calc" / "core.py").write_text("def add(a, b):\n    return a - b\n")
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("[core]\n")
            (root / "blob.bin").write_bytes(b"\x00\xff\x00\xff")
            (root / "big.txt").write_text("x" * (agent.MAX_FILE_BYTES + 1))
            files = agent.gather_files(root)
        self.assertIn("calc/core.py", files)
        self.assertNotIn(".git/config", files)   # vcs dir
        self.assertNotIn("blob.bin", files)      # binary
        self.assertNotIn("big.txt", files)       # oversize


class TestPrompt(unittest.TestCase):
    def test_includes_problem_files_and_the_word_json(self):
        msgs = agent.build_messages("FIX THE BUG", {"calc/core.py": "return a - b"})
        self.assertEqual([m["role"] for m in msgs], ["system", "user"])
        self.assertIn("JSON", msgs[0]["content"])      # required for the API's json response mode
        self.assertIn("FIX THE BUG", msgs[1]["content"])
        self.assertIn("calc/core.py", msgs[1]["content"])


class TestApply(unittest.TestCase):
    def test_writes_full_contents(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "calc").mkdir()
            written = agent.apply_files('{"files": {"calc/core.py": "def add(a, b):\\n    return a + b\\n"}}', root)
            self.assertEqual(written, ["calc/core.py"])
            self.assertIn("a + b", (root / "calc" / "core.py").read_text())

    def test_refuses_path_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(SystemExit) as cm:
                agent.apply_files('{"files": {"../escape.py": "pwned"}}', Path(td))
            self.assertEqual(cm.exception.code, 6)
            self.assertFalse((Path(td).parent / "escape.py").exists())


class TestTransport(unittest.TestCase):
    def test_missing_key_exits_2(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as cm:
                agent.call_openai([{"role": "user", "content": "hi"}])
        self.assertEqual(cm.exception.code, 2)

    def test_success_returns_message_content(self):
        completion = {"choices": [{"message": {"content": '{"files": {"x.py": "ok"}}'}}]}
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True), \
             mock.patch.object(agent.urllib.request, "urlopen",
                               return_value=_Resp(json.dumps(completion).encode())):
            content = agent.call_openai([{"role": "user", "content": "hi"}])
        self.assertEqual(content, '{"files": {"x.py": "ok"}}')

    def test_http_error_summary_drops_message_and_key(self):
        # OpenAI's 401 body echoes a MASKED key fragment in `message`; we must log only type/code
        body = json.dumps({"error": {"message": "Incorrect API key provided: sk-FAKE-xxxxtest",
                                     "type": "invalid_request_error", "code": "invalid_api_key"}}).encode()
        summary = agent._http_error_summary(body)
        self.assertNotIn("sk-", summary)
        self.assertNotIn("Incorrect", summary)        # the human message is dropped entirely
        self.assertIn("invalid_api_key", summary)


if __name__ == "__main__":
    unittest.main()
