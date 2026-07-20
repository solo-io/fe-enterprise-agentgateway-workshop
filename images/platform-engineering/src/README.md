# Diagram sources

HTML sources for the PNGs in `images/platform-engineering/`. Edit the HTML (shared styles live in `diagram-shared.css`), then re-render with headless Chrome from this directory.

Measure the content height (the page stamps it into `data-h` on load):

```bash
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CHROME" --headless=new --disable-gpu --window-size=820,600 --virtual-time-budget=8000 \
  --dump-dom "file://$PWD/centralized-llm-ops-architecture.html" 2>/dev/null | grep -o 'data-h="[0-9]*"'
```

Render at 2x using that height as the window height:

```bash
"$CHROME" --headless=new --disable-gpu --force-device-scale-factor=2 \
  --window-size=820,<data-h> --virtual-time-budget=8000 \
  --screenshot="../centralized-llm-ops-architecture.png" \
  "file://$PWD/centralized-llm-ops-architecture.html"
```

Same two steps for `platform-and-developer-helm-charts-mcp-architecture.html`. Fonts (Figtree, DM Sans, JetBrains Mono) load from Google Fonts, so rendering needs network access. `networking-architecture.html` renders at 1240px wide — use `--window-size=1240,...` in both commands for it.
