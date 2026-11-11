"""Static checks for the agent layer: AGENTS.md, CLAUDE.md, .mcp.json, skills, command."""
import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SKILLS = ["evaluate", "detect-environment", "explain-lab", "diagnose-failure", "ask-the-docs", "adapt-lab",
          "contribute-lab", "stop-slop"]
INTERNAL = "support-agent" + "-tools"


def read(rel):
    return (ROOT / rel).read_text()


def user_files():
    files = [ROOT / "AGENTS.md", ROOT / "CLAUDE.md", ROOT / ".claude/commands/evaluate.md",
             ROOT / ".claude/commands/adapt-lab.md", ROOT / ".claude/commands/contribute-lab.md"]
    files += [ROOT / "skills" / s / "SKILL.md" for s in SKILLS]
    return files


def harness_tools():
    """The harness tools an agent runs: run-e2e.sh, e2e/lib/*.py and *.sh, mint-kubeconfig.sh."""
    tools = [ROOT / "e2e/run-e2e.sh", ROOT / "rbac/mint-kubeconfig.sh"]
    tools += sorted((ROOT / "e2e/lib").glob("*.py")) + sorted((ROOT / "e2e/lib").glob("*.sh"))
    return tools


# A flag a shell tool parses is a `case` pattern: `--out) ...`, `-h|--help)`, `--report=*)`.
SH_CASE = re.compile(r"(?m)^\s*([-a-z|=*]+)\)")
# A flag a Python tool parses is a quoted literal: add_argument("--work") or `a == "--custom"`.
PY_FLAG = re.compile(r"""["'](--[a-z][a-z-]*)["']""")
# A flag on a skill line: `--x` after start of line, a space, a backtick, `(` or `[`.
LINE_FLAG = re.compile(r"(?:^|(?<=[ `(\[]))(--[a-z][a-z-]*)")


def tool_flags(path):
    text = path.read_text()
    if path.suffix == ".py":
        return set(PY_FLAG.findall(text))
    flags = set()
    for pattern in SH_CASE.findall(text):
        for alt in pattern.split("|"):
            alt = alt.split("=", 1)[0]
            if re.fullmatch(r"--[a-z][a-z-]*", alt):
                flags.add(alt)
    return flags


def agent_lines():
    for f in sorted((ROOT / "skills").glob("*/SKILL.md")) + [ROOT / "AGENTS.md"]:
        for n, line in enumerate(f.read_text().splitlines(), 1):
            yield f"{f.relative_to(ROOT)}:{n}", line


def command_units():
    """(where, text) for each command an agent file shows: a line inside a ``` fence, or an
    inline `code span` in prose. A flag belongs to the tool named in the same unit, so prose
    like "`session-log.sh add run` ... without `--keep`" does not pin --keep on session-log.sh."""
    for f in sorted((ROOT / "skills").glob("*/SKILL.md")) + [ROOT / "AGENTS.md"]:
        fenced = False
        for n, line in enumerate(f.read_text().splitlines(), 1):
            where = f"{f.relative_to(ROOT)}:{n}"
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            if fenced:
                yield where, line
            else:
                for span in re.findall(r"`([^`]+)`", line):
                    yield where, span


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
        for rule in ["Never leave anything behind.", "No cluster data leaves the machine except through the user's model provider.",
                     "Scoped, reviewable identity.", "Stop on failure, do not improvise."]:
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
        self.assertIn("## Alpha: Evaluate with an agent", text)
        self.assertIn("[AGENTS.md](AGENTS.md)", text)
        self.assertIn("`/evaluate`", text)
        self.assertLess(text.index("# Table of Contents"), text.index("## Alpha: Evaluate with an agent"))
        self.assertLess(text.index("## Alpha: Evaluate with an agent"), text.index("# Use Cases"))

    def test_command_file(self):
        text = read(".claude/commands/evaluate.md")
        self.assertIn("description: Evaluate Enterprise Agentgateway on the current cluster", text)
        self.assertIn("Read skills/evaluate/SKILL.md and follow it. Arguments: $ARGUMENTS", text)

    def test_adapt_skill_names_every_cli_and_rule(self):
        text = read("skills/adapt-lab/SKILL.md")
        for needle in ["custom-labs/<name>/", "CHANGES.md", "--custom <name> --lint", "--custom <name> --keep",
                       "--custom <name> --report-only", "get_crd_schema", "requirements:", "proves:",
                       "labdoc.py sections", "style-guide.md", "diagnose-failure", "ask-the-docs",
                       "session-log.sh add run", "session-log.sh add keep-custom", "Accepted",
                       "Nothing is written beyond `CHANGES.md`, and no cluster command runs, before the user approves the proposal."]:
            self.assertIn(needle, text)

    def test_adapt_command_points_at_skill(self):
        self.assertIn("Read skills/adapt-lab/SKILL.md and follow it. Arguments: $ARGUMENTS",
                      read(".claude/commands/adapt-lab.md"))

    def test_agents_md_mentions_both_modes_and_custom_labs(self):
        text = read("AGENTS.md")
        for needle in ["skills/adapt-lab/SKILL.md", "/adapt-lab", "custom-labs/", "get_crd_schema"]:
            self.assertIn(needle, text)

    def test_contribute_skill_names_every_cli_and_rule(self):
        text = read("skills/contribute-lab/SKILL.md")
        for needle in ["gh auth status", "git status --porcelain", "e2e/lib/scrub.py", "--from-changes",
                       "e2e/lib/promote.py", "--record", "--undo", "--dry-run", "--lint",
                       "see CHANGELOG (X.Y.Z)", "gh pr create --repo solo-io/fe-enterprise-agentgateway-workshop --base main", "git push -u origin",
                       "style-guide.md", "Nothing is pushed until the user says yes to the diff.",
                       "custom-labs/<name>/", "never edited", "main...X.Y.Z", "staged.patch"]:
            self.assertIn(needle, text)
        for heading in ["## 1. Preflight", "## 2. Scrub", "## 3. Promote", "## 4. Conform",
                        "## 5. Verify as a shipped lab", "## 6. Branch and commit",
                        "## 7. Show the diff and stop", "## 8. Stopping partway"]:
            self.assertIn(heading, text)

    def test_contribute_command_points_at_skill(self):
        text = read(".claude/commands/contribute-lab.md")
        self.assertIn("Read skills/contribute-lab/SKILL.md and follow it. Arguments: $ARGUMENTS", text)
        self.assertIn("custom-labs/", text)

    def test_agents_md_names_three_modes(self):
        text = read("AGENTS.md")
        for needle in ["Evaluate (`skills/evaluate/SKILL.md`", "Adapt (`skills/adapt-lab/SKILL.md`", "Contribute (`skills/contribute-lab/SKILL.md`", "/contribute-lab", "manifest"]:
            self.assertIn(needle, text)

    def test_tool_flag_parsers_find_known_flags(self):
        # Guards the parsers below: if they stop finding flags, the generic check passes vacuously.
        self.assertTrue({"--track", "--custom", "--report", "--report-only", "--uninstall-base", "--help"}
                        <= tool_flags(ROOT / "e2e/run-e2e.sh"))
        self.assertTrue({"--out", "--skip-rbac-check"} <= tool_flags(ROOT / "e2e/lib/detect-env.sh"))
        self.assertTrue({"--from-changes", "--summary"} <= tool_flags(ROOT / "e2e/lib/scrub.py"))
        self.assertIn("--custom", tool_flags(ROOT / "e2e/lib/labdoc.py"))
        self.assertIn("--duration", tool_flags(ROOT / "rbac/mint-kubeconfig.sh"))

    def test_every_flag_on_a_tool_line_exists_in_that_tool(self):
        flags = {t: tool_flags(t) for t in harness_tools()}
        bad, checked = [], 0
        for where, unit in command_units():
            named = [t for t in flags if t.name in unit]
            if not named:
                continue
            for flag in sorted(set(LINE_FLAG.findall(unit))):
                checked += 1
                if not any(flag in flags[t] for t in named):
                    bad.append(f"{where}: {flag} ({', '.join(t.name for t in named)})")
        self.assertGreater(checked, 20, "the check found almost no flags; the unit parser is broken")
        self.assertEqual(bad, [], "flags a skill passes to a harness tool that does not parse them")

    def test_every_harness_path_the_skills_name_exists(self):
        missing = set()
        for where, line in agent_lines():
            for p in re.findall(r"(?:e2e/lib|rbac)/[A-Za-z0-9_.-]+\.(?:sh|py|yaml|md)", line):
                if not (ROOT / p).is_file():
                    missing.add(f"{where}: {p}")
        self.assertEqual(sorted(missing), [])

    def test_no_prospect_and_no_em_dash_in_agent_files(self):
        for f in user_files():
            t = f.read_text()
            self.assertNotIn("prospect", t.lower(), str(f))
            self.assertNotIn("\u2014", t, str(f))


if __name__ == "__main__":
    unittest.main()
