# Develop Vibewords


## Installation

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```


## Running

```bash
task dev
```

This listens on all IPs, so you can access from other devices on your network (e.g. to test from mobile). Open http://localhost:9000 for testing from the local machine.

Alternatively, you can pass custom options as in the following examples:

```bash
# Use a different config file
VIBEWORDS_CONFIG=config.prod.yaml uvicorn vibewords.main:app

# Enable debug logging without editing the file
VIBEWORDS_SERVER_LOG_LEVEL=DEBUG uvicorn vibewords.main:app --reload
```

## Configuration

Configuration is read from `config.yaml` in the working directory (override the path with `VIBEWORDS_CONFIG`). Any option can also be set via environment variable using the pattern `VIBEWORDS_<SECTION>_<FIELD>` — env vars take precedence over the file. See the [example config](./config/config-example.yaml) for details.


## Deployment

Right now this is deployed to GCP.

There is a github actions job that builds an OCI image, pushes to the Artifact Registry, and deploys to Cloud Run on commits to main.


### Manual Deployment

Prerequisites:

- Install docker
- Install `gcloud` CLI

Authenticate to gcloud and configure docker to authenticate to the GCP Artifact Registry:

```shell-script
gcloud auth login
gcloud config set project vibewords-0
gcloud auth configure-docker europe-west2-docker.pkg.dev
```

Build and push container image:

```
docker build . -t europe-west2-docker.pkg.dev/vibeword/vibeword/vibewords:0.1.0
docker push europe-west2-docker.pkg.dev/vibeword/vibeword/vibewords:0.1.0
```

Deploy to GCP, either via the console or:

```bash
gcloud run deploy vibewords \
  --image europe-west2-docker.pkg.dev/vibeword/vibeword/vibewords:0.2.0 \
  --platform managed \
  --region europe-west2 \
  --timeout 3600 \
  --allow-unauthenticated \
  --min-instances=1 \
  --max-instances=1
```

`--timeout 3600` is required — WebSocket connections count as a single HTTP request and will be dropped at the default 60 s limit. Can't set higher than 3600.

If you scale beyond one instance, add `--session-affinity` so WebSocket connections aren't load-balanced across instances (room state is in-memory and not shared).
