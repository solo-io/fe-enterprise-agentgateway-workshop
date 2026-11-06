---
name: explain-lab
description: Say what a lab proves and why a platform team cares, before it runs. Use in enablement mode ahead of each lab.
---

# Explain a lab

Given a lab key such as `routing/configure-routing-openai`:

1. Find its use case in `tracks/<track>.yaml` and read that use case's `value` line.
2. Open `labs/<key>.md` and read the first paragraph after its H1.
3. Say in three to five sentences what capability the lab proves and why a platform team cares. Base it on those two sources and use the prospect's own words for their goals if they gave any.
4. Cite the lab path once, for example `labs/routing/configure-routing-openai.md`, so they can read it themselves.

Do not paste YAML, commands, or lab steps unless the prospect asks. If the lab needs a paid provider key, say so in the last sentence.
