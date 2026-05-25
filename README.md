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

## Deployment

Right now this is deployed to GCP.

There is a github actions job that builds an OCI image, pushes to the Artifact Registry, and deploys to Cloud Run on commits to main.

## Manual Deployment

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

Deploy to GCP, either via the console or:

```bash
gcloud run deploy vibeword \
  --image europe-west2-docker.pkg.dev/vibeword/vibeword/vibeword:0.2.0 \
  --platform managed \
  --region europe-west2 \
  --timeout 3600 \
  --allow-unauthenticated \
  --min-instances=1 \
  --max-instances=1
```

`--timeout 3600` is required — WebSocket connections count as a single HTTP request and will be dropped at the default 60 s limit. Can't set higher than 3600.

If you scale beyond one instance, add `--session-affinity` so WebSocket connections aren't load-balanced across instances (room state is in-memory and not shared).
