"""Static checks for the agent layer: AGENTS.md, CLAUDE.md, .mcp.json, skills, command."""
import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SKILLS = ["evaluate", "detect-environment", "explain-lab", "diagnose-failure", "ask-the-docs"]
INTERNAL = "support-agent" + "-tools"


def read(rel):
    return (ROOT / rel).read_text()


def user_files():
    files = [ROOT / "AGENTS.md", ROOT / "CLAUDE.md", ROOT / ".claude/commands/evaluate.md"]
    files += [ROOT / "skills" / s / "SKILL.md" for s in SKILLS]
    return files


class AgentLayer(unittest.TestCase):
    def test_mcp_json_has_only_solo_knowledge(self):
        data = json.loads(read(".mcp.json"))
        self.assertEqual(list(data["mcpServers"]), ["solo-knowledge"])
        self.assertEqual(data["mcpServers"]["solo-knowledge"],
                         {"type": "http", "url": "https://knowledge-base.soloio-field.com/mcp"})

    def test_no_file_mentions_internal_docs_server(self):
        for f in user_files() + [ROOT / ".mcp.json"]:
            self.assertNotIn(INTERNAL, f.read_text(), str(f))

    def test_each_skill_has_frontmatter_name_and_description(self):
        for s in SKILLS:
            text = read(f"skills/{s}/SKILL.md")
            m = re.match(r"---\n(.*?)\n---\n", text, re.S)
            self.assertIsNotNone(m, s)
            self.assertIn(f"name: {s}\n", m.group(1) + "\n")
            self.assertRegex(m.group(1), r"(?m)^description: \S")

    def test_evaluate_skill_names_every_cli(self):
        text = read("skills/evaluate/SKILL.md")
        for needle in ["./e2e/lib/detect-env.sh", "--resolve-creds", "--install-base --only-base",
                       "--track", "--report", "--report-only", "--uninstall-base", "session-log.sh",
                       "tracks.py list", "explain-lab", "diagnose-failure", "--no-prompt"]:
            self.assertIn(needle, text)

    def test_diagnose_skill_lists_four_options_and_deviation_rule(self):
        text = read("skills/diagnose-failure/SKILL.md")
        for opt in ["- Retry the lab", "- Skip it and continue", "- Work around it live", "- Stop the session"]:
            self.assertIn(opt, text)
        self.assertIn('session-log.sh add deviation <key> "<what was applied and why>"', text)
        self.assertIn("session-log.sh add decision <key> stop", text)
        self.assertIn("session-log.sh add decision <key> skip", text)
        self.assertIn("session-log.sh add decision <key> retry", text)
        for p in ["e2e/.work/<key>/driver.sh", "blocks/NNNN.sh", "blocks/NNNN.out", "result", "outcome"]:
            self.assertIn(p, text)

    def test_agents_md_has_four_hard_rules_and_codex_snippet(self):
        text = read("AGENTS.md")
        for rule in ["Never leave anything behind.", "Nothing about the cluster leaves the cluster.",
                     "Least privilege.", "Stop on failure, do not improvise."]:
            self.assertIn(rule, text)
        self.assertIn("[mcp_servers.solo-knowledge]", text)
        self.assertIn('url = "https://knowledge-base.soloio-field.com/mcp"', text)
        self.assertIn("```toml", text)
        self.assertLess(len(text.splitlines()), 150)

    def test_agents_md_points_at_rbac_readme(self):
        text = read("AGENTS.md")
        self.assertIn("rbac/README.md", text)
        self.assertIn("skills/evaluate/SKILL.md", text)

    def test_no_em_dashes_in_user_prose(self):
        for f in user_files():
            self.assertNotIn("—", f.read_text(), str(f))

    def test_claude_md_is_one_line(self):
        self.assertEqual(read("CLAUDE.md").strip(), "Read AGENTS.md and follow it.")

    def test_agents_md_names_license_key_and_credential_sources(self):
        text = read("AGENTS.md")
        for needle in ["SOLO_TRIAL_LICENSE_KEY", "e2e/.env.local", "shell rc file", "export it in the shell",
                       "export KUBECONFIG=<path>"]:
            self.assertIn(needle, text)

    def test_diagnose_says_harness_already_cleaned_up(self):
        text = read("skills/diagnose-failure/SKILL.md")
        self.assertIn("already ran the lab's Cleanup", text)
        self.assertIn("never run cleanup blocks by hand", text)
        self.assertLess(text.index("add failure"), text.index("Offer exactly these options"))

    def test_evaluate_covers_license_key_duplicates_and_two_tracks(self):
        text = read("skills/evaluate/SKILL.md")
        for needle in ["SOLO_TRIAL_LICENSE_KEY", "duplicate", "last 30 lines", "clears the first track's outcomes"]:
            self.assertIn(needle, text)

    def test_leak_rule_covers_web_and_object_names(self):
        self.assertIn("web search", read("AGENTS.md"))
        self.assertIn("web fetch", read("AGENTS.md"))
        docs = read("skills/ask-the-docs/SKILL.md")
        for needle in ["metadata.name", "web search", "`agentgateway-system`", "`mcp`", "`procurement`",
                       "`stripe-mcp`", "`team-research`", "hostnames and URLs"]:
            self.assertIn(needle, docs)

    def test_evaluate_rig_diagnoses_then_tears_down_then_reports(self):
        text = read("skills/evaluate/SKILL.md")
        rig = text[text.index("## 5. Run the labs"):]
        self.assertIn("./e2e/run-e2e.sh --track <t> --env <class> --no-prompt", rig)
        self.assertNotIn("--no-prompt --report", rig)
        self.assertIn('e2e/lib/session-log.sh add failure <key> "<one-line explanation>"', rig)
        self.assertLess(rig.index("add failure"), rig.index("--uninstall-base"))
        self.assertLess(rig.index("--uninstall-base"), rig.index("--report-only"))
        self.assertIn("env-teardown.json", rig)
        self.assertIn("<YYYYMMDD-HHMMSS>", rig)

    def test_evaluate_starts_session_and_handles_declined_labs(self):
        text = read("skills/evaluate/SKILL.md")
        self.assertLess(text.index("session-log.sh start"), text.index('add decision - "track=<t> mode=<m>"'))
        self.assertIn("session-log.sh add decision <key> declined", text)
        self.assertIn("--skip <key>", text)
        self.assertIn("SKIP (declined)", read("AGENTS.md"))

    def test_explain_lab_has_fallbacks(self):
        text = read("skills/explain-lab/SKILL.md")
        for needle in ["first H2", "`Source:` lines", "`Overview`", "`description:`"]:
            self.assertIn(needle, text)

    def test_agents_md_prereqs_and_env_local_quoting(self):
        text = read("AGENTS.md")
        for needle in ["PyYAML", "bash 4", "quote any value", "<YYYYMMDD-HHMMSS>"]:
            self.assertIn(needle, text)

    def test_readme_links_to_agents_md(self):
        text = read("README.md")
        self.assertIn("## Evaluate with an agent", text)
        self.assertIn("[AGENTS.md](AGENTS.md)", text)
        self.assertIn("`/evaluate`", text)
        self.assertLess(text.index("## Evaluate with an agent"), text.index("# Table of Contents"))

    def test_command_file(self):
        text = read(".claude/commands/evaluate.md")
        self.assertIn("description: Evaluate Enterprise Agentgateway on the current cluster", text)
        self.assertIn("Read skills/evaluate/SKILL.md and follow it. Arguments: $ARGUMENTS", text)


if __name__ == "__main__":
    unittest.main()
