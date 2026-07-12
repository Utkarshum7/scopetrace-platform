/**
 * D5 -- shown only when the backend is running with DEMO_MODE=True (see
 * AuthContext's `demoMode`, sourced from GET /api/me/'s demo_mode field).
 * Tells a visitor this is a portfolio/free-hosting deployment where
 * background work (ingest/calculate/AI) runs synchronously in-process
 * instead of via a Celery Worker/Beat -- see README's "Demo Deployment"
 * section for the full explanation. Absent entirely in production.
 */
export const DemoModeBanner = () => (
  <div
    role="status"
    className="flex items-center justify-center gap-2 px-4 py-1.5 bg-warning-950/30 border-b border-warning-500/20 text-warning-300 text-[11px] font-semibold tracking-wide text-center"
  >
    <span aria-hidden="true">&#9432;</span>
    Demo Mode &mdash; running on free hosting with synchronous background processing, not the production Celery Worker/Beat architecture.
  </div>
);

export default DemoModeBanner;
