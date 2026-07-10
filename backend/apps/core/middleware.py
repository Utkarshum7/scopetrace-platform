"""
Phase 9b -- per-request correlation id.

CONFIRMED gap (Milestone 9b logging audit): no middleware, filter, or
response header tied a log line to the HTTP request that produced it.
Every pipeline task (apps.ingestion.tasks.ingest_task,
apps.carbon.tasks.calculate_task, ...) already threads its own
`workflow_id` through every log line for exactly this reason; the
synchronous request/response path had no equivalent at all -- two log
lines from the same API call were only reconcilable by eyeballing
timestamps and hoping nothing else logged in between.

Generates a fresh id server-side on every request rather than trusting an
incoming X-Request-ID header: this value flows straight into log output,
and nothing in front of this app (Render's edge, a browser) is a trusted
boundary that could be relied on to have already validated/stripped a
client-supplied header.
"""
import uuid

from apps.core.logging_utils import request_id_ctx


class RequestIDMiddleware:
    """Assigns a fresh request id, publishes it to apps.core.logging_utils.
    request_id_ctx (read by RequestIDLogFilter on every log line emitted
    while this request is being handled), and echoes it back as the
    X-Request-ID response header -- so a user reporting an issue can quote
    a single id that support/engineering can grep straight to the matching
    server-side log lines.

    Placed immediately after SecurityMiddleware in settings.MIDDLEWARE so
    it wraps as much of the request/response cycle as possible (every
    later middleware's own logging, the view, DRF's exception handler, all
    benefit from the same id).

    Safe under the gthread worker class (render.yaml/Dockerfile run
    --worker-class gthread --threads 4, i.e. one process, several OS
    threads reusing the same worker across requests): contextvars.
    ContextVar gives each OS thread its own independent value, and this
    always calls .set() at the very start of every request, so a thread
    picking up a new request after finishing a previous one on the SAME
    thread can never see a stale id from that earlier request.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = uuid.uuid4().hex
        request.request_id = request_id
        request_id_ctx.set(request_id)
        response = self.get_response(request)
        response["X-Request-ID"] = request_id
        return response
