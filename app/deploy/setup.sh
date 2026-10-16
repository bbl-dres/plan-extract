#!/usr/bin/env bash
# Install or update the floor plan workflow app on a Debian or Ubuntu server: app/server.py --public as the systemd
# service fpx-app on 127.0.0.1:8765, nginx in front with HTTPS (Let's Encrypt), open to anyone. Run as root; run it
# again to update (git pull, dependencies; the service restarts only when something changed, RESTART=1 forces it).
# Details: app/deploy/README.md.
#
#   sudo DOMAIN=plans.example.ch EMAIL=you@example.ch MODEL_FILE=/tmp/segmenter.pt bash setup.sh
#
#   DOMAIN      the (sub)domain whose DNS A record points at this server (required)
#   EMAIL       Let's Encrypt address for expiry notices (optional)
#   MODEL_FILE  a segmenter.pt copied to the server, installed as the default model, v3 (once)
#   BRANCH      the branch to deploy (default main)
#   RESTART     1: restart the service even when nothing changed
set -euo pipefail

DOMAIN="${DOMAIN:?set DOMAIN, e.g. DOMAIN=plans.example.ch}"
EMAIL="${EMAIL:-}"
MODEL_FILE="${MODEL_FILE:+$(realpath "$MODEL_FILE")}"
REPO_URL="${REPO_URL:-https://github.com/bbl-dres/plan-extract.git}"
BRANCH="${BRANCH:-main}"

SVC=fpx                                                    # service user: owns the checkout, the venv and the jobs
HOME_DIR=/var/lib/fpx
ROOT=/opt/plan-extract
VENV=$HOME_DIR/venv
PY=3.14                                                    # requirements.txt is pinned for Python 3.14
PORT=8765
MODEL=$ROOT/pilot/v2-pipeline/data/model-v3/segmenter.pt   # common.MODEL, the default segmenter
MODEL_SHA256=11fbeb97de291ffce72761adc089b2c9c5d4add970dea914feca89b35844084b   # model v3
REQ=$ROOT/pilot/v2-pipeline/scripts/requirements.txt
SITE=/etc/nginx/sites-available/fpx-app
WEBROOT=/var/www/certbot
ENV_FILE=/etc/fpx-app.env
DEPLOYED=$HOME_DIR/.deployed                               # what the running service was started with (restart only on a change)

step() { printf '\n== %s\n' "$*"; }
as_app() { runuser -u "$SVC" -- env HOME="$HOME_DIR" PATH="$HOME_DIR/.local/bin:/usr/local/bin:/usr/bin:/bin" "$@"; }
# Put a new nginx site in place ($1, a temporary file): kept only when nginx -t passes, else the previous site stays.
install_site() {
  [ -f "$SITE" ] && cp "$SITE" "$SITE.bak"
  install -m 0644 "$1" "$SITE"
  rm -f "$1"
  ln -sf "$SITE" /etc/nginx/sites-enabled/fpx-app
  if ! nginx -t -q; then
    echo "The new nginx site does not pass nginx -t: the previous one stays."
    if [ -f "$SITE.bak" ]; then mv "$SITE.bak" "$SITE"; else rm -f "$SITE" /etc/nginx/sites-enabled/fpx-app; fi
    exit 1
  fi
  rm -f "$SITE.bak"
  # a reload the running nginx refuses (e.g. a rate-limit zone with a new key) is only logged, and the old site stays:
  # a restart then applies it
  local since; since=$(date '+%Y/%m/%d %H:%M:%S')
  systemctl reload-or-restart nginx
  sleep 1
  if awk -v t="$since" '$1 " " $2 >= t && /\[emerg\]/' /var/log/nginx/error.log 2>/dev/null | grep -q .; then
    echo "nginx did not take the new site on reload: restarting it"
    systemctl restart nginx
  fi
}

[ "$(id -u)" -eq 0 ] || { echo "Run as root: sudo DOMAIN=... bash $0"; exit 1; }
command -v apt-get >/dev/null || { echo "This script needs Debian or Ubuntu (apt-get)."; exit 1; }
if [ -n "$MODEL_FILE" ] && [ ! -f "$MODEL_FILE" ]; then echo "MODEL_FILE not found: $MODEL_FILE"; exit 1; fi
cd /                                                       # the service user cannot read root's working directory

step "System packages"
apt-get update -q
GLIB=libglib2.0-0t64                                       # Ubuntu 24.04+ and Debian 13 renamed it; older: libglib2.0-0
apt-cache show "$GLIB" >/dev/null 2>&1 || GLIB=libglib2.0-0
DEBIAN_FRONTEND=noninteractive apt-get install -y -q git curl ca-certificates nginx certbot libgl1 "$GLIB"

step "Service user and code ($REPO_URL, $BRANCH)"
id -u "$SVC" >/dev/null 2>&1 || useradd --system --home-dir "$HOME_DIR" --create-home --shell /usr/sbin/nologin "$SVC"
if [ -d "$ROOT/.git" ]; then
  as_app git -C "$ROOT" fetch --quiet origin "$BRANCH"
  as_app git -C "$ROOT" checkout --quiet "$BRANCH"
  as_app git -C "$ROOT" merge --ff-only --quiet "origin/$BRANCH"
else
  install -d -o "$SVC" -g "$SVC" "$ROOT"
  as_app git clone --quiet --branch "$BRANCH" "$REPO_URL" "$ROOT"
fi
as_app git -C "$ROOT" log -1 --format='at %h %s (%cd)'

step "Python $PY and the pipeline's requirements (CPU build of torch)"
[ -x "$HOME_DIR/.local/bin/uv" ] || as_app sh -c 'curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh'
UV=$HOME_DIR/.local/bin/uv
[ -x "$VENV/bin/python" ] || as_app "$UV" venv --quiet --python "$PY" "$VENV"
TORCH=$(sed -n 's/^torch==\([0-9][^ #]*\).*/\1/p' "$REQ")
VISION=$(sed -n 's/^torchvision==\([0-9][^ #]*\).*/\1/p' "$REQ")
[ -n "$TORCH" ] && [ -n "$VISION" ] || { echo "No torch==<version> or torchvision==<version> line in $REQ"; exit 1; }
# both from the CPU index, "+cpu" pinned: a PyPI torchvision next to the CPU torch fails with "torchvision::nms does not exist"
as_app "$UV" pip install --quiet --python "$VENV/bin/python" "torch==$TORCH+cpu" "torchvision==$VISION+cpu" \
  --index-url https://download.pytorch.org/whl/cpu
grep -vE '^torch(vision)?==' "$REQ" | as_app tee "$HOME_DIR/requirements-server.txt" >/dev/null
as_app "$UV" pip install --quiet --python "$VENV/bin/python" -r "$HOME_DIR/requirements-server.txt"
"$VENV/bin/python" --version

step "Segmenter model"
if [ -n "$MODEL_FILE" ]; then
  install -d -o "$SVC" -g "$SVC" "$(dirname "$MODEL")"
  install -o "$SVC" -g "$SVC" -m 0640 "$MODEL_FILE" "$MODEL"
fi
if [ -f "$MODEL" ]; then
  SUM=$(sha256sum "$MODEL" | cut -d' ' -f1)
  if [ "$SUM" = "$MODEL_SHA256" ]; then echo "$MODEL: model v3, checksum ok"
  else echo "WARNING: $MODEL is not model v3 (sha256 $SUM): copy the right file and run again with MODEL_FILE=<path>."; fi
else
  echo "WARNING: no model at $MODEL. Copy segmenter.pt to the server and run this script again with MODEL_FILE=<path>."
fi

step "Data folder and OCR models (downloaded now: the service runs with a read-only venv)"
install -d -o "$SVC" -g "$SVC" -m 0750 "$ROOT/app/data" "$HOME_DIR/.cache"
as_app "$VENV/bin/python" -c "import sys; sys.path.insert(0, '$ROOT/app'); import bridge; bridge.engine(); print('OCR engine ready')" \
  || echo "WARNING: the OCR engine did not start; the app's pipeline check shows why."

step "Service fpx-app"
# The settings file is written from this template at every run (systemd takes a whole line as the value: no comment
# after a value). Operator overrides go into $ENV_FILE.local, which the unit reads after it; both count for the restart.
cat > "$ENV_FILE" <<EOF
# Settings of the fpx-app service, written by app/deploy/setup.sh at every run: do not edit. Overrides go into
# $ENV_FILE.local (one NAME=value per line, no comment after a value), then: systemctl restart fpx-app
FPX_APP_THREADS=$(nproc)
FPX_APP_WORKERS=2
# The names the app reads (defaults):
# FPX_APP_THREADS        torch and OpenCV threads (the server's cores)
# FPX_APP_WORKERS        worker processes for the extractions; 0: a thread of the server (app/worker.py)
# FPX_APP_JOB_TIMEOUT    1200  seconds a worker process may spend on one extraction or read-ahead
# FPX_APP_KEEP_MINUTES   60    a sheet is deleted this long after its last request
# FPX_APP_MAX_BUSY       10    new work is refused while this many sheets wait or run
# FPX_APP_MAX_PAGES      10    pages of a PDF (frames of an image) a sheet may have
# FPX_APP_SESSIONS       3     analysed sheets kept in memory, and FPX_APP_SESSION_MB (1500) of their rasters at most
# V2_MODEL               another segmenter than the default (model v3), a path
EOF
touch "$ENV_FILE.local"
install -m 0644 "$ROOT/app/deploy/fpx-app.service" /etc/systemd/system/fpx-app.service
systemctl daemon-reload
systemctl enable --quiet fpx-app
# a restart stops the sheets being processed: only when the code, the unit, the settings or the model changed, and
# after the sheets in work are done (up to 5 minutes; RESTART=1 restarts at once)
FINGERPRINT=$( { as_app git -C "$ROOT" rev-parse HEAD; cat /etc/systemd/system/fpx-app.service "$ENV_FILE" "$ENV_FILE.local"; echo "${SUM:-}"; } | sha256sum | cut -d' ' -f1)
RESTARTED=0
if [ "${RESTART:-}" = 1 ] || ! systemctl is-active --quiet fpx-app || [ "$(cat "$DEPLOYED" 2>/dev/null)" != "$FINGERPRINT" ]; then
  if [ "${RESTART:-}" != 1 ] && systemctl is-active --quiet fpx-app; then
    for _ in $(seq 60); do
      BUSY=$(curl -fsS "http://127.0.0.1:$PORT/api/status" 2>/dev/null | "$VENV/bin/python" -c "import json, sys; print(json.load(sys.stdin).get('busy', 0))" 2>/dev/null || echo 0)
      [ "$BUSY" = 0 ] && break
      echo "  waiting for $BUSY sheet(s) in work before the restart"
      sleep 5
    done
  fi
  systemctl restart fpx-app
  RESTARTED=1
  echo "fpx-app (re)started"
else
  echo "fpx-app unchanged: not restarted"
fi

step "HTTPS certificate for $DOMAIN and the nginx site"
install -d "$WEBROOT"
rm -f /etc/nginx/sites-enabled/default                     # nginx.conf's catch-all answers other names (with nothing)
if [ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
  # until the certificate exists, port 80 answers only the Let's Encrypt challenge: the app is never served over HTTP
  cat > "$SITE.new" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ { root $WEBROOT; }
    location / { return 404; }
}
EOF
  install_site "$SITE.new"
  if [ -n "$EMAIL" ]; then ACCOUNT=(-m "$EMAIL"); else ACCOUNT=(--register-unsafely-without-email); fi
  if ! certbot certonly --webroot -w "$WEBROOT" -d "$DOMAIN" --non-interactive --agree-tos "${ACCOUNT[@]}" \
       --deploy-hook "systemctl reload nginx"; then
    echo "No certificate. Check that the DNS A record of $DOMAIN points at this server (it resolves to:"
    echo "$(getent ahostsv4 "$DOMAIN" | awk '{print $1}' | sort -u | paste -sd' ' || true)) and that port 80 is open, then run this script again."
    exit 1
  fi
fi
# nginx before 1.25.1 (Ubuntu 24.04 has 1.24) has no "http2 on": HTTP/2 goes on the listen lines there
NGINX_V=$(nginx -v 2>&1 | sed -n 's|.*nginx/\([0-9.]*\).*|\1|p')
HTTP2=()
if [ "$(printf '%s\n' 1.25.1 "$NGINX_V" | sort -V | head -1)" != 1.25.1 ]; then
  HTTP2=(-e '/^    http2 on;/d' -e 's|^\(    listen .*443 ssl\);|\1 http2;|')
fi
sed -e "s|__DOMAIN__|$DOMAIN|g" -e "s|__WEBROOT__|$WEBROOT|g" -e "s|__ROOT__|$ROOT|g" "${HTTP2[@]}" "$ROOT/app/deploy/nginx.conf" > "$SITE.new"
install_site "$SITE.new"

step "Pipeline check"
HEALTH=$(mktemp)
for _ in $(seq 60); do
  curl -fsS "http://127.0.0.1:$PORT/api/health" -o "$HEALTH" 2>/dev/null && break
  sleep 1
done
if [ ! -s "$HEALTH" ]; then
  rm -f "$HEALTH"
  echo "FAILED: the app does not answer on 127.0.0.1:$PORT. See: journalctl -u fpx-app -n 50"
  exit 1
fi
if ! "$VENV/bin/python" - "$HEALTH" <<'EOF'
import json, sys
h = json.load(open(sys.argv[1], encoding="utf-8"))
for name, item in h["items"].items():
    print(f"  {'ok     ' if item['ok'] else 'MISSING'}  {name}: {item.get('detail', '')}")
print("pipeline ready" if h["ok"] else "pipeline NOT ready: see the lines above")
sys.exit(0 if h["ok"] else 1)
EOF
then
  rm -f "$HEALTH"
  exit 1
fi
rm -f "$HEALTH"
[ "$RESTARTED" = 1 ] && echo "$FINGERPRINT" > "$DEPLOYED"   # recorded only once the restarted app answers: a failed run restarts again

printf '\nOpen https://%s/   ·   logs: journalctl -u fpx-app -f   ·   update: run this script again\n' "$DOMAIN"
