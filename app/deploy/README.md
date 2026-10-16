# Deploying the workflow app on a server

Runs the [workflow app](../README.md) on a Debian 12 or Ubuntu 24.04 (or newer) VPS (e.g. at Infomaniak) as a public site: [`app/server.py --public`](../server.py) as the systemd service `fpx-app`, nginx in front of it with HTTPS (Let's Encrypt). No login; uploaded sheets are deleted 60 minutes after their last request.

    browser ──HTTPS──▶ nginx :443 ──▶ app/server.py --public on 127.0.0.1:8765 ──▶ pipeline (fpx) in two worker processes, two sheets at a time

The app itself listens on 127.0.0.1 only; nginx is the only way in, and it answers only the app's domain (other names and the bare IP get no answer). It must sit at the root of its own (sub)domain, because the page calls `/api/...` with absolute paths. The root of the repository's GitHub Pages site forwards to it.

## Before you start

- **Server:** SSH access with sudo; 16 GB RAM recommended (the server keeps up to 1.5 GB of analysed rasters, each of the two worker processes needs about 2 GB while it extracts); about 10 GB free disk plus room for uploads.
- **Domain:** a (sub)domain, e.g. `plans.example.ch`, with a DNS A record pointing at the server's IPv4 address. Add an AAAA record only if the server has IPv6. At Infomaniak: Manager → Domains → DNS zone.
- **Ports:** 80 and 443 open to the internet. If the VPS firewall in the Infomaniak Manager is on, allow both there.
- **Model:** the segmenter weights are gitignored; copy them from a machine that has them: `pilot/v2-pipeline/data/model-v3/segmenter.pt`.

## Install

1. Copy the model to the server:

       scp pilot/v2-pipeline/data/model-v3/segmenter.pt <user>@<server>:segmenter.pt

2. On the server, fetch the setup script and run it:

       curl -fsSLO https://raw.githubusercontent.com/bbl-dres/plan-extract/main/app/deploy/setup.sh
       sudo DOMAIN=plans.example.ch EMAIL=you@example.ch MODEL_FILE=$HOME/segmenter.pt bash setup.sh

   It ends with the pipeline check and the address to open. Without a DNS record yet, it installs everything and stops before the certificate; run it again once the domain resolves.

[`setup.sh`](setup.sh) installs nginx, certbot and git; creates the user `fpx`; clones the repository to `/opt/plan-extract`; installs Python 3.14 (with uv) and the pipeline's [requirements](../../pilot/v2-pipeline/scripts/requirements.txt) with the CPU builds of torch and torchvision in `/var/lib/fpx/venv`; installs the model and downloads the OCR models; starts [`fpx-app`](fpx-app.service); gets the certificate; and writes the [nginx site](nginx.conf).

## Operate

| Task | Command |
|---|---|
| Update to the latest `main` | Run `setup.sh` again with the same variables. It restarts the app only when something changed (`RESTART=1` forces it); a restart keeps the sheets, but a sheet being processed fails and has to be run again. |
| Logs | `journalctl -u fpx-app -f` (no file names or drawing titles: they stay in the job's own log, deleted with it) |
| Restart | `sudo systemctl restart fpx-app` |
| Pipeline check | `curl -s http://127.0.0.1:8765/api/health` on the server (visitors get only what is there, not paths or versions); queue and memory: `/api/status` |
| Settings | `/etc/fpx-app.env` is written by `setup.sh` at every run (do not edit); overrides go into `/etc/fpx-app.env.local` (one `NAME=value` per line, no comment after a value: systemd takes the whole line), then `systemctl restart fpx-app`. Names: `FPX_APP_THREADS` (the cores), `FPX_APP_WORKERS` (2 extraction worker processes; 0: a thread of the server), `FPX_APP_JOB_TIMEOUT` (1200 s per extraction), `FPX_APP_KEEP_MINUTES` (60), `FPX_APP_MAX_BUSY` (10), `FPX_APP_MAX_PAGES` (10), `FPX_APP_SESSIONS` (3) and `FPX_APP_SESSION_MB` (1500), `V2_MODEL` |
| Data | `/opt/plan-extract/app/data/public`: the sheets being worked on, each with its upload, outputs and OCR cache |

The certificate renews automatically (certbot's systemd timer).

## What the public server keeps

- **Sheets:** each sheet job is deleted 60 minutes after its last request, also across restarts (the folder's time records the last request). "Start over" in the app deletes the project's sheets at once.
- **No sheet list:** a visitor reaches only the sheets whose ids their own browser holds; the ids are random (128 bits).
- **No access log:** nginx logs nothing per request and only critical errors (a request line would quote upload names); the app's console names no files or drawings, only routes and kinds of errors.

## Limits

- **Two lanes, two extraction workers:** the analysis of an upload and a scale step run on a thread of the server (seconds), so a newcomer's sheet is analysed while others are extracted; extractions and the read-ahead of the drawing text run in `FPX_APP_WORKERS` (2) worker processes, each with its own segmenter, so two sheets are extracted at once (each about 1.7 × slower than alone, since the segmentation uses every core; the segmenter takes all cores while one worker is busy, half each with two). A job that waits is told its place and the expected wait. While a visitor confirms the area and the scale, a free worker reads the text inside their drawings ahead of the extraction (about 30 s on 8 cores); the extraction then takes about 30 s on the demo sheet, 60 s when the text could not be read ahead (a changed area, a scale far from the proposal, a sheet confirmed within seconds). A worker process that dies is replaced; the job it held fails with that reason. A second visitor's sheet waits ("waiting for the worker"), at most one read-ahead long.
- **Protection against floods:** per address 6 new sheets a minute (a burst of 5 more) and 12 pipeline runs a minute (scale or extraction, burst 10); no new work while 10 sheets wait or run, or below 2 GB of free disk; work queued for a visitor who left (no request for 3 minutes) is skipped.
- **Size of a sheet:** at most 300 MB, 10 pages and 160 megapixels per page, checked from the file header before anything is decoded, and a working image of at most 40 megapixels at the confirmed scale; the service may use three quarters of the server's memory (systemd). A worker process that the kernel kills for memory is replaced and only its sheet fails (`OOMPolicy=continue`); the server itself restarts if it is the one killed.
- **Plans leave the visitor's machine:** they are on the server until deleted. Non-public BBL plans need BBL's approval for this host.
- **PyMuPDF is AGPL-3.0:** a public network service must offer its source to its users; the repository is public, but check the licence question (see the note in `requirements.txt`).
