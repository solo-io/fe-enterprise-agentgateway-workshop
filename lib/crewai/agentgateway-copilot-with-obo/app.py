import base64
import io
import json
import os
import queue
import re
import sys
import threading

# Suppress CrewAI's interactive prompts before any crewai import.
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("CREWAI_DISABLE_PACKAGE_CHECK", "1")

import logging
import requests

# mcp 1.26.0 warns on HTTP 202 session termination — 202 is correct per MCP spec, warning is a false alarm.
logging.getLogger("mcp.client.streamable_http").setLevel(logging.ERROR)

import streamlit as st
from crewai import Agent, Crew, LLM, Process, Task

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mGKHF]")
# Matches: Tool ask_question executed with result: <snippet>
_TOOL_EXEC_RE = re.compile(r"Tool (\S+) executed with result:\s*(.{0,120})")
# Matches repo name from read_wiki_structure result
_REPO_RE = re.compile(r"Available pages for ([^:\n]+):")
# Lines to suppress: transient LLM retry noise (400 probe requests) and event-bus
# mismatch warnings that accompany them — these don't affect the final result.
_SUPPRESS_RE = re.compile(
    r"\[CrewAIEventsBus\] Warning:"
    r"|An unknown error occurred\. Please check"
    r"|Error details: Error code: 4\d\d"
)


def _decode_jwt_payload(token: str) -> dict:
    """Decode the payload of a JWT without verification."""
    seg = token.split(".")[1]
    seg = seg.replace("-", "+").replace("_", "/")
    padding = 4 - len(seg) % 4
    if padding != 4:
        seg += "=" * padding
    return json.loads(base64.b64decode(seg).decode("utf-8"))


class _StdoutQueue(io.TextIOBase):
    """Forwards each non-empty stdout line from the crew thread into the output queue."""

    def __init__(self, q: queue.Queue, original):
        self._q = q
        self._original = original
        self._buf = ""

    def write(self, s: str) -> int:
        self._buf += _ANSI_RE.sub("", s)
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            stripped = line.strip()
            if stripped and not _SUPPRESS_RE.search(stripped):
                self._original.write(line + "\n")
                self._q.put(("step", stripped))
                m = _TOOL_EXEC_RE.search(stripped)
                if m:
                    tool_name = m.group(1)
                    snippet = m.group(2).strip()
                    repo = ""
                    repo_m = _REPO_RE.search(snippet)
                    if repo_m:
                        repo = repo_m.group(1).strip()
                    self._q.put(("tool_exec", {
                        "tool": tool_name,
                        "repo": repo,
                        "snippet": snippet,
                    }))
        return len(s)

    def flush(self):
        self._original.flush()


st.set_page_config(page_title="Agentgateway Copilot (OBO)", layout="wide")
st.title("Agentgateway Copilot (OBO)")
st.caption("Powered by agentgateway + CrewAI + DeepWiki MCP + Solo.io Docs MCP — with OBO delegation")

# ─── Sidebar: Keycloak login and OBO token exchange ──────────────────────────
with st.sidebar:
    st.subheader("Identity")
    username = st.text_input("Username", value="testuser")
    password = st.text_input("Password", value="testuser", type="password")
    if st.button("Log in"):
        keycloak_url = "http://localhost:8080"
        actor_token = os.environ.get("ACTOR_TOKEN", "")
        if not actor_token:
            st.error("ACTOR_TOKEN env var is not set. Start the demo via demo-script.sh.")
        else:
            try:
                # Step 1: Get user JWT from Keycloak
                token_resp = requests.post(
                    f"{keycloak_url}/realms/obo-realm/protocol/openid-connect/token",
                    data={
                        "username": username,
                        "password": password,
                        "grant_type": "password",
                        "client_id": "agw-client",
                        "client_secret": "agw-client-secret",
                    },
                )
                token_resp.raise_for_status()
                user_jwt = token_resp.json()["access_token"]

                # Step 2: Exchange user JWT + k8s SA actor token for delegated OBO token
                sts_resp = requests.post(
                    "http://localhost:7777/token",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                        "subject_token": user_jwt,
                        "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
                        "actor_token": actor_token,
                        "actor_token_type": "urn:ietf:params:oauth:token-type:jwt",
                    },
                )
                sts_resp.raise_for_status()
                obo_jwt = sts_resp.json()["access_token"]

                # Step 3: Store both tokens so the sidebar can show the before/after
                user_payload = _decode_jwt_payload(user_jwt)
                st.session_state["obo_jwt"] = obo_jwt
                st.session_state["user_jwt"] = user_jwt
                st.session_state["user_iss"] = user_payload.get("iss", "")
                st.session_state["user_sub"] = user_payload.get("sub", "")
                st.rerun()
            except Exception as exc:
                st.error(f"Login failed: {exc}")

    if "obo_jwt" in st.session_state:
        obo_payload = _decode_jwt_payload(st.session_state["obo_jwt"])
        st.success("Logged in — OBO token active")

        st.caption("User JWT (from Keycloak)")
        st.json({
            "iss": st.session_state.get("user_iss", ""),
            "sub": st.session_state.get("user_sub", ""),
        })

        st.caption("OBO token (from agentgateway STS)")
        st.json({
            "iss": obo_payload.get("iss", ""),
            "sub": obo_payload.get("sub", ""),
            "act": obo_payload.get("act", {}),
        })

        st.divider()
        st.caption("Verify token enforcement")
        gateway_ip_sidebar = os.environ.get("GATEWAY_IP", "")
        if st.button("Probe gateway with both tokens") and gateway_ip_sidebar:
            probe_url = f"http://{gateway_ip_sidebar}:8080/openai"
            probe_body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]}
            col1, col2 = st.columns(2)
            with col1:
                st.caption("User JWT (Keycloak)")
                try:
                    r = requests.post(probe_url, headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {st.session_state['user_jwt']}",
                    }, json=probe_body, timeout=5)
                    if r.status_code == 200:
                        st.success(f"HTTP {r.status_code}")
                    else:
                        st.error(f"HTTP {r.status_code}")
                        st.code(r.text[:300])
                except Exception as e:
                    st.error(str(e))
            with col2:
                st.caption("OBO token (STS)")
                try:
                    r = requests.post(probe_url, headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {st.session_state['obo_jwt']}",
                    }, json=probe_body, timeout=5)
                    if r.status_code == 200:
                        st.success(f"HTTP {r.status_code}")
                    else:
                        st.error(f"HTTP {r.status_code}")
                        st.code(r.text[:300])
                except Exception as e:
                    st.error(str(e))

        st.divider()
        if st.button("Log out"):
            for key in ("obo_jwt", "user_jwt", "user_iss", "user_sub"):
                st.session_state.pop(key, None)
            st.rerun()

# ─── Main form ────────────────────────────────────────────────────────────────
with st.form("crew_form"):
    question = st.text_input(
        "Question",
        value=os.environ.get("CREW_QUESTION", "What is agentgateway?"),
        placeholder="e.g. What is agentgateway?",
    )
    gateway_ip = st.text_input(
        "Gateway IP / Hostname",
        value=os.environ.get("GATEWAY_IP", ""),
        placeholder="e.g. 192.168.1.100",
    )
    st.caption(f"MCP tool calls route through agentgateway → http://{gateway_ip or '<gateway>'}:8080/agw-copilot/mcp (multiplexed: DeepWiki + Solo.io Docs)")
    submitted = st.form_submit_button("Ask Expert", type="primary")

if submitted:
    if "obo_jwt" not in st.session_state:
        st.warning("Log in with Keycloak first (sidebar →)")
        if gateway_ip:
            # Fire an unauthenticated request to show the JWT policy rejecting it live.
            try:
                probe = requests.post(
                    f"http://{gateway_ip}:8080/openai",
                    headers={"Content-Type": "application/json"},
                    json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
                    timeout=5,
                )
                st.error(f"Agentgateway rejected the request: **HTTP {probe.status_code}**")
                st.code(probe.text, language="json")
            except Exception as exc:
                st.error(f"Could not reach gateway: {exc}")
        st.stop()

    if not gateway_ip:
        st.error("Gateway IP is required.")
        st.stop()

    if not question:
        st.error("Please enter a question.")
        st.stop()

    # Capture session state values on the main thread before spawning the background thread;
    # st.session_state is not safely accessible from non-Streamlit threads.
    obo_jwt = st.session_state["obo_jwt"]

    output_queue: queue.Queue = queue.Queue()

    def task_callback(task_output):
        agent = getattr(task_output, "agent", "Agent")
        description = getattr(task_output, "description", None) or str(task_output)
        output_queue.put(("task", (agent, description)))

    def run_crew():
        # Subscribe to crewai tool events to capture full tool arguments.
        try:
            from crewai.utilities.events import crewai_event_bus
            from crewai.utilities.events.tool_usage_events import (
                ToolUsageStarted,
                ToolUsageFinished,
            )

            @crewai_event_bus.on(ToolUsageStarted)
            def _on_tool_start(source, event):
                pass  # event bus doesn't carry MCP tool args; handled via stdout

            @crewai_event_bus.on(ToolUsageFinished)
            def _on_tool_end(source, event):
                output_queue.put(("tool_done", getattr(event, "tool_name", "")))

        except Exception:
            pass  # event bus unavailable; fall back to stdout parsing

        orig_stdout = sys.stdout
        sys.stdout = _StdoutQueue(output_queue, orig_stdout)
        try:
            from crewai_tools import MCPServerAdapter

            # OBO token is validated by the JWT policy on the gateway;
            # agentgateway injects the real OpenAI API key from the backend secretRef.
            llm = LLM(
                provider="openai",
                base_url=f"http://{gateway_ip}:8080/openai",
                model="gpt-4o-mini",
                api_key=obo_jwt,
            )

            mcp_url = f"http://{gateway_ip}:8080/agw-copilot/mcp"
            with MCPServerAdapter({
                "url": mcp_url,
                "transport": "streamable-http",
                "headers": {"Authorization": f"Bearer {obo_jwt}"},
            }) as adapter:
                expert = Agent(
                    role="Agentgateway Copilot",
                    goal=(
                        "Answer questions about agentgateway accurately and thoroughly "
                        "by consulting both the Solo.io official product documentation and "
                        "the enterprise agentgateway workshop materials."
                    ),
                    backstory=(
                        "A knowledgeable expert on agentgateway who uses two MCP sources "
                        "multiplexed through agentgateway: DeepWiki for the workshop and GitHub repos, "
                        "and the Solo.io docs search for official product documentation. "
                        "Provides accurate answers with YAML and config examples drawn from both sources."
                    ),
                    llm=llm,
                    tools=list(adapter),
                    verbose=True,
                )

                task = Task(
                    description=(
                        f"Question: '{question}'\n\n"
                        f"Tools available:\n"
                        f"  deepwiki: deepwiki_read_wiki_structure, deepwiki_ask_question\n"
                        f"  soloiodocs: soloiodocs_search, soloiodocs_get_chunks\n\n"
                        f"Steps:\n"
                        f"1. deepwiki_read_wiki_structure(repoName='solo-io/fe-enterprise-agentgateway-workshop') "
                        f"— find the most relevant page name.\n"
                        f"2. deepwiki_ask_question(repoName='solo-io/fe-enterprise-agentgateway-workshop') "
                        f"— ask a focused question explicitly requesting YAML, referencing the page from step 1.\n"
                        f"3. soloiodocs_search(query=<relevant terms>, product='solo-enterprise-for-agentgateway' "
                        f"or 'standalone-agentgateway-oss') — find matching Solo.io official docs. "
                        f"If a result is incomplete, call soloiodocs_get_chunks for more context.\n"
                        f"4. Optionally deepwiki_ask_question(repoName='agentgateway/agentgateway') "
                        f"for reference links only — never use it as a source of YAML.\n\n"
                        f"Rules:\n"
                        f"- Provide full context from tool output: descriptions, explanations, prerequisites, "
                        f"and enablement details — not just YAML. Help the reader understand what the config does and why.\n"
                        f"- YAML, commands, and config must be quoted exactly from tool output.\n"
                        f"- After every YAML block, add two lines:\n"
                        f"  > Source: [Workshop — solo-io/fe-enterprise-agentgateway-workshop, <page>] "
                        f"or [Solo.io Docs — <product>, <section>]\n"
                        f"  > Confidence: <score 1-10>/10 — <one sentence explaining why, "
                        f"e.g. exact match from tool output, partial match, inferred from context>\n"
                        f"- If neither source has the detail, say so — do not guess."
                    ),
                    expected_output=(
                        "A concise answer with exact YAML and config examples from tool output, "
                        "each followed by a source citation and a confidence score (1-10) with reasoning."
                    ),
                    agent=expert,
                )

                crew = Crew(
                    agents=[expert],
                    tasks=[task],
                    process=Process.sequential,
                    task_callback=task_callback,
                )

                result = crew.kickoff()
                output_queue.put(("final", str(result)))
        except Exception as e:
            output_queue.put(("error", str(e)))
        finally:
            sys.stdout = orig_stdout
            output_queue.put(("done", None))

    thread = threading.Thread(target=run_crew, daemon=True)
    thread.start()

    st.subheader("Agent Activity")
    with st.expander("Live steps", expanded=True):
        steps_placeholder = st.empty()

    with st.expander("Tools called", expanded=True):
        tools_placeholder = st.empty()

    final_placeholder = st.empty()

    steps: list[str] = []
    completed_tasks: list[str] = []
    # Each entry: {"tool": name, "repo": str, "snippet": str}
    tool_log: list[dict] = []

    def _render_tools():
        if not tool_log:
            return
        lines = []
        for i, entry in enumerate(tool_log, 1):
            tool_name = entry.get("tool", "unknown")
            repo = entry.get("repo", "")
            snippet = entry.get("snippet", "")
            line = f"[{i}] {tool_name}"
            if repo:
                line += f"\n    repo   : {repo}"
            if snippet and not repo:
                # show first 100 chars of result when repo isn't parseable
                line += f"\n    result : {snippet[:100]}"
            lines.append(line)
        tools_placeholder.code("\n\n".join(lines), language=None)

    while True:
        try:
            msg_type, content = output_queue.get(timeout=120)
        except queue.Empty:
            st.warning("Timed out waiting for the expert to respond.")
            break

        if msg_type == "done":
            break
        elif msg_type == "error":
            st.error(f"Error: {content}")
            break
        elif msg_type == "step":
            steps.append(content)
            steps_placeholder.code("\n".join(steps), language=None)
        elif msg_type == "tool_exec":
            tool_log.append(content)
            _render_tools()
        elif msg_type == "task":
            agent, description = content
            completed_tasks.append(content)
            st.info(f"Task {len(completed_tasks)} finished — **{agent}**: {description}")
        elif msg_type == "final":
            final_placeholder.empty()
            with final_placeholder.container():
                st.subheader("Answer")
                st.markdown(content)
                st.download_button(
                    label="Download answer",
                    data=content,
                    file_name="agentgateway_answer.md",
                    mime="text/markdown",
                )

    st.success("Done.")
