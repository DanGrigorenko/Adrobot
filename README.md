# Adrobot

A local web interface for creating Keitaro campaigns and editing stream offers.
Make changes in a persistent draft, review them, and publish only when you're ready.

[![Release](https://img.shields.io/github/v/release/DanGrigorenko/Adrobot)](https://github.com/DanGrigorenko/Adrobot/releases)
[![Python](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/Docker-Compose-blue.svg)](https://docs.docker.com/compose/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## Install

Requires Docker Engine, Docker Compose v2.24.4+ or v5, and access to a Keitaro
Admin API. Python and Node.js are not required to run the application.

Clone the repository and create a local configuration:

```console
$ git clone https://github.com/DanGrigorenko/Adrobot.git
$ cd Adrobot
$ cp .env.example .env
```

Edit `.env` and set your Keitaro URL and API key. To create campaigns, also set
IDs for an existing domain, campaign group, and traffic source.

```console
$ docker compose up --build -d
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). To check that the app is
running:

```console
$ curl --fail http://127.0.0.1:8000/api/health
```

The health check covers the application, not connectivity to Keitaro.

## Usage

**Edit a stream:** search for a campaign by name or ID, open a supported
stream, then change offer shares or add and remove offers. Changes are saved
locally as a draft. Review the result and explicitly confirm **Push** to write
it to Keitaro. Refreshing or cancelling a draft does not publish it.

**Create a campaign:** open Creator, enter the campaign name and GEOs, select an
offer, and review the proposed configuration. Confirm to create the campaign
and its streams. Creator settings must be configured first.

If a publication or creation response is lost, the app keeps the operation and
provides a way to check its outcome. A result that cannot be established is not
reported as a success or automatically retried.

## Configuration

| Variable | Purpose |
| --- | --- |
| `KEITARO_BASE_URL` | Root URL of your Keitaro instance, without `/admin_api/v1`; use HTTPS for remote instances. |
| `KEITARO_API_KEY` | Server-side Admin API key with the permissions needed to read campaigns, streams, offers and settings, and to create campaigns or update streams. |
| `CREATOR_DOMAIN_ID` | ID of an existing domain for new campaigns. |
| `CREATOR_GROUP_ID` | ID of an existing campaign group. |
| `CREATOR_TRAFFIC_SOURCE_ID` | ID of an existing traffic source. |
| `CREATOR_NAME_PREFIX` | Optional prefix for new campaign names. |
| `APP_ORIGIN` | Exact browser origin, without a trailing slash; defaults to `http://127.0.0.1:8000`. |

The three Creator IDs are required to create campaigns, but not to use the
editor. See [`.env.example`](.env.example) for the complete configuration.
Never commit or share `.env`: it contains your API key.

## Data and safety

- Drafts and operation history are stored in the `sqlite-data` Docker volume;
  database migrations run at startup.
- `docker compose down` keeps the volume. **`docker compose down --volumes`
  deletes the database.** Back up the stopped database before upgrading.
- The app is designed for one instance and one backend worker. Compose binds
  it to localhost; do not expose it to the internet. Use an SSH tunnel for
  remote access.
- Tests use a fake Keitaro, not a live instance. Live integration and routing
  require separate, authorized verification. The unverified stream preview
  link is not implemented.

## Development

Requires Python 3.13 and Node.js 22. From the repository root:

```console
$ python3 -m venv .venv
$ .venv/bin/pip install -r requirements-dev.txt
$ .venv/bin/ruff check --select F app tests
$ .venv/bin/python -m pytest -q
$ npm ci --prefix frontend
$ npm run build --prefix frontend
```

The frontend build includes TypeScript type checking. Ruff checks Python for
Pyflakes errors; Python type checking is not currently configured.

To test the packaged application against a local fake Keitaro, run:

```console
$ COMPOSE_TEST=1 .venv/bin/python -m pytest -m compose -q
```

This test requires Docker and free localhost ports 18080 and 18081. It creates
and removes the `adrobot-issue15-test` Compose project **and its volume**;
do not use that project name for your own data.

CI runs these checks for pushes and pull requests targeting `develop` and
`master`. A `vX.Y.Z` tag on a commit in `master`'s history runs the checks
again before publishing a GitHub Release with generated notes. The tag is the
application version; the version in `frontend/package.json` is private
frontend package metadata. There is no automated server deployment or image
publishing.

## License

[MIT](LICENSE) — Copyright (c) 2026 Daniil Grigorenko.
