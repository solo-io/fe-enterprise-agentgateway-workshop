from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

import os


def main():

    gateway_ip = os.environ.get('GATEWAY_IP')

    if not gateway_ip:
        raise ValueError("GATEWAY_IP environment variable is not set")

    base_url = f"http://{gateway_ip}:8080/openai/v1"
    print(f"Using base_url: {base_url}")

    topic = os.environ.get('AGENT_TOPIC', 'AI Gateway key patterns and concepts')
    print(f"Research topic: {topic}")

    # agentgateway proxies to OpenAI; use a placeholder key — agentgateway handles real auth
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        base_url=base_url,
        api_key="agentgateway-handles-auth",
    )

    parser = StrOutputParser()

    # ── Researcher chain ────────────────────────────────────────────────────
    researcher_prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            "You are a curious and thorough researcher who can explore any subject — "
            "technical or non-technical — and surface the most compelling facts, "
            "ideas, and insights.",
        ),
        (
            "human",
            "Research the topic: '{topic}'. "
            "Identify at least 4 interesting findings, facts, or perspectives. "
            "Keep notes concise and focused on what would engage a general reader.",
        ),
    ])

    researcher_chain = researcher_prompt | llm | parser

    # ── Writer chain ─────────────────────────────────────────────────────────
    writer_prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            "You are a versatile writer who crafts clear, lively blog posts for a broad "
            "audience — from complete beginners to seasoned experts — without "
            "jargon or unnecessary complexity.",
        ),
        (
            "human",
            "Using the research notes below, write a blog post of 100-200 words on the topic: '{topic}'. "
            "The post should have a punchy title, be engaging and easy to read for anyone "
            "from a curious beginner to an experienced professional, and end with a memorable takeaway.\n\n"
            "Research notes:\n{research}",
        ),
    ])

    writer_chain = writer_prompt | llm | parser

    # ── Sequential execution (Researcher → Writer) ────────────────────────
    print("\n> Running Researcher agent...\n")
    research = researcher_chain.invoke({"topic": topic})
    print(research)

    print("\n> Running Writer agent...\n")
    blog_post = writer_chain.invoke({"topic": topic, "research": research})

    print("\n" + "=" * 60)
    print("FINAL BLOG POST:")
    print("=" * 60)
    print(blog_post)


if __name__ == '__main__':
    main()
