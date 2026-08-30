# Webscraping examples

## PS5 Slim Disc deal monitor

`ps5_monitor.py` performs one OLX Portugal check, sends at most three unseen matches to ntfy, records successful notifications in SQLite, and exits. Run it every 15 minutes with the scheduler on your server.

It accepts listings only when they are active, in EUR, from €200 up to but not including €300, approximately within 80 km of Penafiel, and clearly identify a PS5 Slim with a disc drive. A successful check with no unseen matches sends no notification.

### Install

Python 3.9 or newer is required.

```bash
python3 -m venv webscrapping-env
source webscrapping-env/bin/activate
pip install requests
```

### Configure

All values are optional; these are the defaults:

```bash
export NTFY_TOPIC=eduardo_notifications
export MIN_PRICE_EUR=200
export MAX_PRICE_EUR=300
export MAX_DISTANCE_KM=80
export STATE_DB_PATH=ps5_monitor.sqlite3
```

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
*/15 * * * * cd /srv/webscrapping-examples && NTFY_TOPIC=eduardo_notifications STATE_DB_PATH=/var/lib/ps5-monitor/state.sqlite3 /usr/bin/flock -n /tmp/ps5-monitor.lock webscrapping-env/bin/python ps5_monitor.py >> ps5_monitor.log 2>&1
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
