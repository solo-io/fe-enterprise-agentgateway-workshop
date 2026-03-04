from crewai import Agent, Task, Crew, Process, LLM
import os


def main():
    gateway_ip = os.environ.get("GATEWAY_IP")
    if not gateway_ip:
        raise ValueError("GATEWAY_IP environment variable is not set")

    base_url = f"http://{gateway_ip}:8080/openai"
    topic = os.environ.get("CREW_TOPIC", "AI Gateway key patterns and concepts")

    print(f"Using base_url: {base_url}")
    print(f"Research topic: {topic}")

    # agentgateway returns an OpenAI-compatible format, so use provider="openai"
    # The /openai path prefix is rewritten to /v1/chat/completions by the HTTPRoute
    agentgateway_proxy = LLM(
        provider="openai",
        base_url=base_url,
        model="gpt-4o-mini",
        api_key="agentgateway-handles-auth",  # agentgateway injects the real key
    )

    researcher = Agent(
        role="Researcher",
        goal="Gather interesting and accurate information on any topic",
        backstory=(
            "A curious and thorough researcher who can explore any subject — "
            "technical or non-technical — and surface the most compelling facts, "
            "ideas, and insights."
        ),
        llm=agentgateway_proxy,
    )

    writer = Agent(
        role="Blog Writer",
        goal="Turn research into an engaging blog post anyone can enjoy",
        backstory=(
            "A versatile writer who crafts clear, lively blog posts for a broad "
            "audience — from complete beginners to seasoned experts — without "
            "jargon or unnecessary complexity."
        ),
        llm=agentgateway_proxy,
    )

    research_task = Task(
        description=(
            f"Research the topic: '{topic}'. "
            "Identify at least 4 interesting findings, facts, or perspectives. "
            "Keep notes concise and focused on what would engage a general reader."
        ),
        expected_output=(
            f"A bullet-point list of 4 or more findings about '{topic}', "
            "each with a one or two sentence explanation."
        ),
        agent=researcher,
    )

    writing_task = Task(
        description=(
            f"Using the research notes, write a blog post of 100-200 words on the topic: '{topic}'. "
            "The post should have a punchy title, be engaging and easy to read for anyone "
            "from a curious beginner to an experienced professional, and end with a memorable takeaway."
        ),
        expected_output=(
            "A short blog post with a title, 2-3 paragraphs, and a closing takeaway. "
            "No jargon. 100-200 words."
        ),
        agent=writer,
        context=[research_task],
    )

    crew = Crew(
        agents=[researcher, writer],
        tasks=[research_task, writing_task],
        verbose=True,
        process=Process.sequential,
    )

    result = crew.kickoff()
    print("\n" + "=" * 60)
    print("FINAL BLOG POST:")
    print("=" * 60)
    print(result)


if __name__ == "__main__":
    main()
