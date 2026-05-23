# VibeWord

Collaborative crossword solving in real time. Multiple players share a room, see each other's cursors and highlights, and solve together.

Supports `.ipuz` files and Guardian crosswords by URL.

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Running locally

```bash
uvicorn vibeword.main:app --reload
```

Open http://localhost:8000. Either drag in an `.ipuz` file or paste a Guardian crossword URL (e.g. `https://www.theguardian.com/crosswords/cryptic/30013`).

## Debug logging

```bash
export LOG_LEVEL=DEBUG
uvicorn vibeword.main:app --reload
```

`INFO` (default) logs room creation, player joins/disconnects, and renames. `DEBUG` additionally logs every cell update and cursor move.

## Guardian scraper (standalone)

Download a Guardian crossword as an `.ipuz` file without opening a room:

```bash
python scripts/guardian_to_ipuz.py https://www.theguardian.com/crosswords/cryptic/30013
python scripts/guardian_to_ipuz.py 30013                        # cryptic by default
python scripts/guardian_to_ipuz.py cryptic/30013 -o puzzle.ipuz
python scripts/guardian_to_ipuz.py 30013 --no-solutions         # omit answers
```

## Saving and resuming progress

Click **⬇ Export** in the toolbar to download the current grid as an `.ipuz` file with a `saved` field. Drag it back onto the landing page to resume — the room will open with all previous answers pre-filled.

## Deploying to Cloud Run

```bash
gcloud builds submit --tag gcr.io/YOUR_PROJECT/vibeword

gcloud run deploy vibeword \
  --image gcr.io/YOUR_PROJECT/vibeword \
  --platform managed \
  --region europe-west1 \
  --timeout 3600 \
  --allow-unauthenticated
```

`--timeout 3600` is required — WebSocket connections count as a single HTTP request and will be dropped at the default 60 s limit.

If you scale beyond one instance, add `--session-affinity` so WebSocket connections aren't load-balanced across instances (room state is in-memory and not shared).

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8080` | Port the server listens on (set automatically by Cloud Run) |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
