"""
Phase 7.5 (H4-8) -- a minimal, additive DRF EXCEPTION_HANDLER.

CONFIRMED gap: no EXCEPTION_HANDLER was configured, so any exception DRF's
own default handler doesn't recognize (an APIException subclass, Http404,
PermissionDenied) fell straight through to Django's own uncaught-exception
machinery -- a non-JSON response inconsistent with every other endpoint's
envelope, and (were DEBUG ever accidentally True in production) a full
stack trace served to the client.

Deliberately narrow: this ONLY changes behavior for exceptions that
PROPAGATE OUT of a view uncaught and that DRF's own default handler can't
already map to a Response. It does not touch:
  - Any of the many `return Response({"detail": ...}, status=...)` calls
    throughout this codebase's views -- those never reach an exception
    handler at all, since the view returned normally.
  - Any DRF-recognized exception (ValidationError, PermissionDenied,
    NotFound, Http404, etc.) -- exception_handler() already maps those
    correctly; this function only acts when that returns None.
So this closes exactly the "genuinely unexpected exception" gap (H4-8's
"inconsistent error envelope" finding) without redesigning the many
already-working, deliberately-shaped error responses across the API --
that broader consistency pass is separate, larger-scope work.
"""
import logging

from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_default_exception_handler

logger = logging.getLogger(__name__)


def unhandled_exception_handler(exc, context):
    response = drf_default_exception_handler(exc, context)
    if response is not None:
        return response

    view = context.get("view")
    request = context.get("request")
    # Phase 9b: request.request_id (apps.core.middleware.RequestIDMiddleware)
    # is already attached to this line automatically, via the 'console'
    # handler's request_id logging filter -- method/path are added directly
    # here since the filter only has access to the LogRecord, not the DRF
    # `request` this handler is closed over.
    logger.exception(
        "Unhandled exception in %s: %s %s",
        getattr(view, "__class__", type(view)).__name__,
        getattr(request, "method", "?"),
        getattr(request, "path", "?"),
    )
    return Response(
        {"detail": "An unexpected error occurred."},
        status=500,
    )
