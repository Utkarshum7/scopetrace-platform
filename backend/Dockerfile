# ScopeTrace API — production container image (Phase 5j: multi-stage).
#
# Two stages: `deps` installs Python packages into an isolated location,
# `runtime` copies only that installed-package tree plus an EXPLICIT
# allow-list of application source directories — never `COPY . .`. This is
# deliberate, not cosmetic: a blanket `COPY . .` would silently pull in
# whatever happens to exist in the local build context at build time (a
# throwaway dev venv, editor/tool caches, local media files) regardless of
# .dockerignore's correctness. An explicit COPY list can't leak anything it
# doesn't name, which is the real, structural benefit here — psycopg2-binary
# is the only remotely "compiled" dependency this project has, and it ships
# precompiled wheels, so there's no build-toolchain weight to strip out the
# way multi-stage more commonly justifies itself.
FROM python:3.12-slim AS deps

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Only requirements.txt is needed to resolve this layer — copying just this
# file (not the rest of the source) means Docker's layer cache is reused for
# every rebuild that doesn't touch dependencies, which is most of them.
COPY requirements.txt .
# --user installs into /root/.local, a self-contained tree the runtime stage
# below copies wholesale — no venv needed for a single-purpose container.
RUN pip install --no-cache-dir --user -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/root/.local/bin:$PATH \
    # `pip install --user` resolves its target directory from $HOME at
    # install time (root's, in the `deps` stage: /root/.local). Site
    # packages are looked up from $HOME the same way at *import* time too —
    # since this stage still runs as root when USER appuser hasn't been
    # reached yet, and appuser's own $HOME would otherwise default to
    # /home/appuser (a directory that doesn't have the packages), pinning
    # HOME=/root here is what makes appuser's Python still find them.
    HOME=/root

WORKDIR /app

COPY --from=deps /root/.local /root/.local

# Explicit allow-list — everything the application actually needs to run,
# and nothing else. New top-level application directories must be added
# here deliberately; that's the point.
COPY apps/ ./apps/
COPY config/ ./config/
COPY manage.py entrypoint.sh requirements.txt ./

# Normalise line endings + make the entrypoint executable (robust across
# Windows checkouts), then drop to a non-root user. beat-data is pre-created
# here (empty) so its ownership is already correct when docker-compose.yml's
# `beat` service mounts a named volume onto it — Docker seeds a fresh named
# volume from whatever already exists in the image at that path, permissions
# included; without this, the volume would appear owned by root and appuser
# (the Celery Beat process) couldn't write its schedule file there.
RUN sed -i 's/\r$//' /app/entrypoint.sh \
    && chmod +x /app/entrypoint.sh \
    && adduser --disabled-password --gecos "" appuser \
    && mkdir -p /app/beat-data \
    && chown -R appuser:appuser /app \
    # /root is 700 (root-only) by default — HOME=/root above correctly
    # points appuser's Python at /root/.local/lib/.../site-packages, but
    # appuser still can't traverse into /root at all without this. a+rX
    # grants read + (for directories only) execute/search to every user,
    # without changing ownership or granting write access appuser doesn't
    # need — the packages stay root-owned and read-only from appuser's POV.
    && chmod -R a+rX /root
USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120"]
