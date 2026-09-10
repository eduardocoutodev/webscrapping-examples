# Webscraping examples

## PS5 Slim Disc deal monitor

`ps5_monitor.py` performs one OLX Portugal check, sends at most three unseen matches to ntfy, records successful notifications in SQLite, and exits. Run it every 15 minutes with the scheduler on your server.

It accepts listings only when they are active, in EUR, from €200 up to but not including €300, approximately within 80 km of Penafiel, and clearly identify a PS5 Slim with a disc drive. A successful check with no unseen matches sends no notification.

### Install

Python 3.9 or newer is required.

```bash
python3 -m venv webscrapping-env
source webscrapping-env/bin/activate
pip install requests python-dotenv
```

### Configure

Copy the example file, then edit `.env`. The script loads it automatically:

```bash
cp .env.example .env
```

The monitor settings and defaults are:

```dotenv
NTFY_TOPIC=eduardo_notifications
MIN_PRICE_EUR=200
MAX_PRICE_EUR=300
MAX_DISTANCE_KM=80
OLX_MAX_RESULTS=200
STATE_DB_PATH=ps5_monitor.sqlite3
```

OLX accepts no more than 40 results per request. `OLX_MAX_RESULTS=200` therefore makes five paginated requests and deduplicates promoted listings returned on multiple pages. Values from 1 through 300 are accepted; 200 is the recommended balance for a 15-minute schedule.

`MIN_PRICE_EUR` and `MAX_PRICE_EUR` now control both the OLX query and the stricter local filter. The maximum remains exclusive locally, so `MAX_PRICE_EUR=400` accepts prices below €400.

`eduardo_notifications` is a public, guessable ntfy topic. Anyone who knows it can read or publish messages. Use a hard-to-guess topic if this becomes a concern.

The SQLite database stores only notified OLX listing IDs and notification timestamps. Keep it across deployments to prevent duplicate alerts. If it becomes corrupt, the command exits with an error instead of silently resetting it.

### Run once

```bash
webscrapping-env/bin/python ps5_monitor.py
```

Exit status `0` means the check completed, including when it found no match. An OLX, ntfy, configuration, or SQLite failure is written to stderr and returns a non-zero status.

### Test

```bash
webscrapping-env/bin/python -m unittest discover -s tests -v
```

Tests mock OLX and ntfy and do not send network requests.

### Schedule every 15 minutes on Linux

Create the state directory once:

```bash
sudo install -d -o "$USER" -g "$USER" /var/lib/ps5-monitor
```

Add this entry with `crontab -e`, replacing `/srv/webscrapping-examples` with the repository path:

```cron
*/15 * * * * cd /srv/webscrapping-examples && STATE_DB_PATH=/var/lib/ps5-monitor/state.sqlite3 /usr/bin/flock -n /tmp/ps5-monitor.lock webscrapping-env/bin/python ps5_monitor.py >> ps5_monitor.log 2>&1
```

`flock` prevents overlapping invocations. The script has bounded HTTP timeouts and does not contain its own polling loop.

## Other examples

### Requirements

-   Python 3.6+

### Installation

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

### Usage

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
