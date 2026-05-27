import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llm_reviewer import codex_runner, poller


class InlineReviewTests(unittest.TestCase):
    def test_extract_json_findings_from_plain_array(self):
        raw = json.dumps(
            [
                {
                    "type": "issue",
                    "severity": "blocking",
                    "category": "correctness",
                    "file": "src/A.java",
                    "line": 12,
                    "body": "Impact: bad\nEvidence: changed line\nFix: change it",
                }
            ]
        )

        findings = poller.extract_findings(raw)

        self.assertEqual(1, len(findings))
        self.assertEqual("src/A.java", findings[0]["file"])

    def test_runner_does_not_forward_stdin_to_child_processes(self):
        completed = subprocess.CompletedProcess(["cmd"], 0, "", "")
        with patch("llm_reviewer.poller.subprocess.run", return_value=completed) as mocked:
            poller.run(["cmd"])

        self.assertIs(mocked.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_codex_runner_does_not_forward_stdin_to_codex(self):
        completed = subprocess.CompletedProcess(["codex"], 0, "[]", "")
        with patch("llm_reviewer.codex_runner.subprocess.run", return_value=completed) as mocked:
            with patch.object(codex_runner, "PROMPT_FILE", Path("/Users/ajay/github/llm-reviewer/prompts/00-meta.md")):
                with tempfile.TemporaryDirectory() as tmp:
                    with patch.object(codex_runner, "LOG_DIR", Path(tmp)):
                        result = codex_runner.main()

        self.assertEqual(0, result)
        self.assertIs(mocked.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_extract_json_findings_can_be_capped(self):
        raw = json.dumps(
            [
                {"file": f"src/{idx}.java", "line": idx, "body": "Fix it"}
                for idx in range(10)
            ]
        )

        findings = poller.extract_findings(raw, max_findings=3)

        self.assertEqual(3, len(findings))
        self.assertEqual("src/2.java", findings[-1]["file"])

    def test_review_prompt_uses_structured_contract_not_raw_body(self):
        prompt = poller.review_prompt(
            "example/enabled-repo",
            {
                "web_url": "https://gitlab.com/example/enabled-repo/-/merge_requests/269",
                "iid": 269,
                "title": "Move auth",
                "source_branch": "feature",
                "target_branch": "master",
                "sha": "abc",
            },
            {"max_findings_per_review": 8},
        )

        self.assertIn("Use the `code-review` skill", prompt)
        self.assertIn("title", prompt)
        self.assertIn("impact", prompt)
        self.assertIn("evidence", prompt)
        self.assertIn("fix", prompt)
        self.assertIn("confidence", prompt)
        self.assertIn("Return at most 8 findings", prompt)
        self.assertNotIn("- body", prompt)

    def test_finding_body_renders_review_contract_fields(self):
        body = poller.finding_body(
            {
                "type": "issue",
                "severity": "blocking",
                "category": "correctness",
                "title": "offset is now required",
                "impact": "Existing clients fail before reaching the repository.",
                "evidence": "The changed @RequestParam lacks a defaultValue.",
                "fix": "Restore the default value.",
                "confidence": 0.98,
            }
        )

        self.assertIn("**Issue (blocking, correctness):** offset is now required", body)
        self.assertIn("\n\n**Impact:** Existing clients fail", body)
        self.assertIn("\n\n**Evidence:** The changed", body)
        self.assertIn("\n\n**Fix:** Restore", body)
        self.assertIn("\n\n**Confidence:** 0.98", body)

    def test_codex_wrapper_builds_superpower_skill_task(self):
        task = codex_runner.review_task_prompt("Review MR 269")

        self.assertIn("/using-superpowers", task)
        self.assertIn("$code-reviewer", task)
        self.assertIn("GitLab MCP", task)
        self.assertIn("Review MR 269", task)

    def test_codex_wrapper_uses_configured_noninteractive_defaults(self):
        cmd = codex_runner.codex_command()

        self.assertEqual("codex", cmd[0])
        self.assertIn("--ask-for-approval", cmd)
        self.assertIn("never", cmd)
        self.assertIn("--profile", cmd)
        self.assertIn("llm-reviewer", cmd)
        self.assertNotIn("--output-schema", cmd)
        self.assertIn("--skip-git-repo-check", cmd)
        self.assertNotIn("--output-last-message", cmd)


    def test_extract_json_findings_from_codex_transcript_with_duplicate_arrays(self):
        raw = """OpenAI Codex v0.132.0
codex
[{\"type\":\"finding\",\"severity\":\"high\",\"category\":\"correctness\",\"title\":\"one\",\"file\":\"src/A.java\",\"line\":12,\"evidence\":\"x\"}]
tokens used
63,272
[{\"type\":\"finding\",\"severity\":\"high\",\"category\":\"correctness\",\"title\":\"one\",\"file\":\"src/A.java\",\"line\":12,\"evidence\":\"x\"}]
"""

        findings = poller.extract_findings(raw)

        self.assertEqual(1, len(findings))
        self.assertEqual("one", findings[0]["title"])

    def test_review_prompt_pins_review_contract_vocabulary(self):
        prompt = poller.review_prompt(
            "example/enabled-repo",
            {"web_url": "url", "iid": 1, "title": "t", "source_branch": "s", "target_branch": "t", "sha": "abc"},
            {"max_findings_per_review": 5},
        )

        self.assertIn("type must be one of: issue, suggestion, question", prompt)
        self.assertIn("severity must be one of: blocking, non-blocking", prompt)
        self.assertIn("confidence must be a number from 0 to 1", prompt)

    def test_read_config_defaults_max_findings_to_five(self):
        original_config = poller.CONFIG
        with tempfile.TemporaryDirectory() as tmp:
            try:
                poller.CONFIG = Path(tmp) / "env.toml"
                poller.CONFIG.write_text(
                    """
gitlab_url = "https://gitlab.com"

[[projects]]
path = "example/enabled-repo"
enabled = true
""",
                    encoding="utf-8",
                )

                cfg = poller.read_config()

                self.assertEqual(5, cfg["max_findings_per_review"])
            finally:
                poller.CONFIG = original_config

    def test_meta_prompt_limit_is_rendered_from_config(self):
        prompt = "Return JSON.\nMaximum {{MAX_FINDINGS_PER_REVIEW}} findings total for the packet.\n"

        rendered = poller.render_meta_prompt(prompt, 8)

        self.assertIn("Maximum 8 findings total", rendered)
        self.assertNotIn("{{MAX_FINDINGS_PER_REVIEW}}", rendered)

    def test_codex_runner_reads_new_max_findings_config_name(self):
        original_config = codex_runner.ENV_CONFIG
        with tempfile.TemporaryDirectory() as tmp:
            try:
                codex_runner.ENV_CONFIG = Path(tmp) / "env.toml"
                codex_runner.ENV_CONFIG.write_text(
                    """
[review]
max_findings_per_merge_request = 9
""",
                    encoding="utf-8",
                )

                self.assertEqual(9, codex_runner.configured_max_findings())
            finally:
                codex_runner.ENV_CONFIG = original_config

    def test_extract_json_findings_from_fenced_block(self):
        raw = '```json\n[{"file":"src/A.java","line":12,"body":"Fix it"}]\n```'

        findings = poller.extract_findings(raw)

        self.assertEqual(12, findings[0]["line"])

    def test_changed_lines_parser_tracks_new_lines_only(self):
        diff = {
            "new_path": "src/A.java",
            "old_path": "src/A.java",
            "diff": "@@ -10,2 +10,3 @@\n old\n+new one\n same\n+new two\n",
        }

        changed = poller.changed_lines_from_diffs([diff])

        self.assertIn(11, changed["src/A.java"]["new_lines"])
        self.assertIn(13, changed["src/A.java"]["new_lines"])
        self.assertNotIn(10, changed["src/A.java"]["new_lines"])

    def test_build_position_requires_changed_line(self):
        mr = {"diff_refs": {"base_sha": "b", "start_sha": "s", "head_sha": "h"}}
        changed = {
            "src/A.java": {
                "old_path": "src/A.java",
                "new_path": "src/A.java",
                "new_lines": {12},
            }
        }

        position = poller.build_position(mr, changed, {"file": "src/A.java", "line": 12})

        self.assertEqual("text", position["position_type"])
        self.assertEqual(12, position["new_line"])
        self.assertIsNone(poller.build_position(mr, changed, {"file": "src/A.java", "line": 99}))

    def test_fingerprint_is_stable_for_same_finding(self):
        finding = {"file": "src/A.java", "line": 12, "body": "Impact: bad\nFix: yes"}

        one = poller.finding_fingerprint("p", 1, "abc", finding)
        two = poller.finding_fingerprint("p", 1, "abc", finding)

        self.assertEqual(one, two)
        self.assertEqual(64, len(one))

    def test_mcp_thread_args_url_encode_project_and_string_iid(self):
        args = poller.mcp_thread_args(
            "example/enabled-repo",
            269,
            "body",
            {"position_type": "text", "new_line": 12},
        )

        self.assertEqual("example%2Fenabled-repo", args["project_id"])
        self.assertEqual("269", args["merge_request_iid"])
        self.assertEqual("body", args["body"])
        self.assertEqual(12, args["position"]["new_line"])

    def test_post_inline_finding_uses_mcp_discussion_id(self):
        with patch("llm_reviewer.poller.mcp_call_tool", return_value={"id": "disc-1"}):
            discussion_id = poller.post_inline_finding(
                {"gitlab_url": "https://gitlab.com"},
                "token",
                "group/repo",
                1,
                "body",
                {"position_type": "text"},
            )

        self.assertEqual("disc-1", discussion_id)

    def test_post_inline_finding_falls_back_to_existing_body_match(self):
        with patch("llm_reviewer.poller.mcp_call_tool", return_value={}):
            with patch("llm_reviewer.poller.find_discussion_by_body", return_value="disc-existing") as finder:
                with patch("llm_reviewer.poller.create_merge_request_discussion") as creator:
                    discussion_id = poller.post_inline_finding(
                        {"gitlab_url": "https://gitlab.com"},
                        "token",
                        "group/repo",
                        1,
                        "body",
                        {"position_type": "text"},
                    )

        self.assertEqual("disc-existing", discussion_id)
        finder.assert_called_once()
        creator.assert_not_called()

    def test_post_inline_finding_falls_back_to_rest_create(self):
        with patch("llm_reviewer.poller.mcp_call_tool", return_value={}):
            with patch("llm_reviewer.poller.find_discussion_by_body", return_value=""):
                with patch("llm_reviewer.poller.create_merge_request_discussion", return_value={"id": "disc-rest"}):
                    discussion_id = poller.post_inline_finding(
                        {"gitlab_url": "https://gitlab.com"},
                        "token",
                        "group/repo",
                        1,
                        "body",
                        {"position_type": "text"},
                    )

        self.assertEqual("disc-rest", discussion_id)


if __name__ == "__main__":
    unittest.main()
