# ScopeTrace — Frontend

The React + Vite single-page application for **ScopeTrace**, the Enterprise ESG Compliance & AI Governance Platform. It provides the analyst-facing dashboard, the multi-source ingestion (upload) center, the emissions review ledger with the record approval workflow, and the governed AI insights surfaces (anomaly explanations, factor recommendations, the ESG assistant, and AI cost/observability widgets).

## Tech Stack

- **React 18** + **Vite 5** (fast HMR, ES module build)
- **Tailwind CSS 3** for styling
- **Axios** for the API layer (`src/services/api.js`)

## Getting Started

```bash
# Install dependencies
npm install

# Start the dev server (http://localhost:5173)
npm run dev

# Production build
npm run build

# Preview the production build locally
npm run preview
```

## Configuration

Create a `.env` file from `.env.example` and point it at the backend API:

```ini
VITE_API_URL=http://localhost:8000
```

## Project Structure

```
src/
  pages/        Dashboard, Upload Center, Review Ledger
  components/   StatusBadge, FilterBar, ApprovalModal
  services/     api.js — axios client + endpoint wrappers
```

See the repository root `README.md` for the full platform architecture, backend setup, and deployment details.
