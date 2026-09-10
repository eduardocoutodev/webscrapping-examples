### Webscraping examples with Python and Selenium

# Requirements

-   Python 3.6+

# Installation

Create a Python virtual env to make sure that dependencies does not have conflicts to local dependencies.

```bash
python3 -m venv webscrapping-env
```

Activate the created virtual env

```bash
source webscrapping-env/bin/activate
```

Run the following command to install the dependencies:

```bash
pip3 install -r requirements.txt
```

# Usage

```bash
python3 <script_name>.py
```

Example:

```bash
python3 top-250-imdb.py
```

## Vinted PS5 deal ranking

Set `OPENROUTER_API_KEY` in `.env`, then run:

```bash
webscrapping-env/bin/python rank_vinted_deals.py \
  --pages 5 \
  --requests-per-minute 30
```

The live catalog and item-detail content is stored in `vinted_deals.sqlite3`.
The AI-ranked export is written to `final-results.json`; progress is printed to
the terminal while the run is active.

The ranking only keeps PS5 Disc listings from €280 to €400. Sellers must have
at least one review and a rating of 3 stars or higher. Each result includes its
`console_type` (`slim_disc` or `fat_disc`) and the matching `deal` tier.

The default model is `openai/gpt-5.6-luna`. Before ranking, the script reads
its live OpenRouter price and refuses to start if the conservative worst-case
cost exceeds the default `$1.50` run limit. Each response records its actual
OpenRouter cost; `final-results.json` includes the actual cost, reserved maximum,
and run limit. Override the ceiling with `--max-cost-usd`.

Ranking requests use a strict JSON schema, require the provider to honor request
parameters and price caps, and enable OpenRouter response healing. Use
`--debug-llm` to save raw model responses under `llm-debug/` when diagnosing a
provider response. The model research and cost comparison is cached in
`plan/research-openrouter-models.md`.
