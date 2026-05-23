# VibeWord

Collaborative crossword solving in real time. Multiple players share a room, see each other's cursors and highlights, and solve together.

Supports `.ipuz` files and Guardian crosswords by URL.

## Dev Setup

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

Open http://localhost:8000.

## Debug logging

```bash
export LOG_LEVEL=DEBUG
uvicorn vibeword.main:app --reload
```

## Guardian scraper (standalone)

This is a CLI tool to download a Guardian crossword as an `.ipuz` file 


## Deploying

Right now this is deployed on GCP via Cloud Run. The container image is pushed to a repository in the GCP Artifact Registry.

Prerequisites:

- Install docker
- Install `gcloud` CLI

Authenticate to gcloud and configure docker to authenticate to the GCP Artifact Registry:

```shell-script
gcloud auth login
gcloud config set project vibeword
gcloud auth configure-docker europe-west2-docker.pkg.dev
```

Build and push container image:

```
docker build . -t europe-west2-docker.pkg.dev/vibeword/vibeword/vibeword:0.1.0
docker push europe-west2-docker.pkg.dev/vibeword/vibeword/vibeword:0.1.0
```

Deploy to GCP:

Either via the console or:

```bash
gcloud run deploy vibeword \
  --image gcr.io/YOUR_PROJECT/vibeword \
  --platform managed \
  --region europe-west1 \
  --timeout 3600 \
  --allow-unauthenticated
```

`--timeout 3600` is required — WebSocket connections count as a single HTTP request and will be dropped at the default 60 s limit.

If you scale beyond one instance, add `--session-affinity` so WebSocket connections aren't load-balanced across instances (room state is in-memory and not shared).
