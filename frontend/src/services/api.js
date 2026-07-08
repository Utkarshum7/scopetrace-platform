import axios from 'axios';

// Get base URL from Vite environment variables, default to Django local development port
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

// ---------------------------------------------------------------------------
// Token storage (JWT access + refresh). localStorage keeps the session across
// reloads; the access token is short-lived and refreshed on demand.
// ---------------------------------------------------------------------------
const ACCESS_KEY = 'scopetrace_access';
const REFRESH_KEY = 'scopetrace_refresh';

export const tokenStore = {
  getAccess: () => localStorage.getItem(ACCESS_KEY),
  getRefresh: () => localStorage.getItem(REFRESH_KEY),
  set: (access, refresh) => {
    if (access) localStorage.setItem(ACCESS_KEY, access);
    if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear: () => {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
};

// AuthContext registers a callback invoked when the session becomes
// irrecoverable (refresh failed / expired) so it can force a logout.
let onAuthFailure = () => {};
export const setAuthFailureHandler = (fn) => {
  onAuthFailure = fn;
};

const api = axios.create({
  baseURL: API_BASE_URL,
  // Fail requests after 60s instead of hanging forever (tolerates cold starts).
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Attach the access token to every request.
api.interceptors.request.use((config) => {
  const token = tokenStore.getAccess();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// On a 401, transparently refresh the access token once and retry. A single
// in-flight refresh is shared across concurrent requests.
let refreshing = null;
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;
    const status = error.response?.status;
    const url = original?.url || '';
    const isAuthCall = url.includes('/api/auth/');

    if (status === 401 && original && !original._retry && !isAuthCall && tokenStore.getRefresh()) {
      original._retry = true;
      try {
        if (!refreshing) {
          refreshing = axios
            .post(`${API_BASE_URL}/api/auth/refresh/`, { refresh: tokenStore.getRefresh() })
            .then((res) => {
              tokenStore.set(res.data.access, res.data.refresh);
              return res.data.access;
            })
            .finally(() => {
              refreshing = null;
            });
        }
        const newAccess = await refreshing;
        original.headers.Authorization = `Bearer ${newAccess}`;
        return api(original);
      } catch (refreshError) {
        tokenStore.clear();
        onAuthFailure();
        return Promise.reject(refreshError);
      }
    }

    console.error('API Error:', error.response?.data || error.message);
    return Promise.reject(error);
  }
);

// Ingestion + Auth API Helper Wrappers
export const apiService = {
  // ----- Authentication -----
  async login(username, password) {
    const response = await api.post('/api/auth/login/', { username, password });
    tokenStore.set(response.data.access, response.data.refresh);
    return response.data;
  },

  async logout() {
    const refresh = tokenStore.getRefresh();
    try {
      if (refresh) {
        await api.post('/api/auth/logout/', { refresh });
      }
    } catch (err) {
      // Best-effort server-side blacklist; always clear locally.
      console.error('Logout error (ignored):', err.response?.data || err.message);
    }
    tokenStore.clear();
  },

  async getCurrentUser() {
    const response = await api.get('/api/me/');
    return response.data;
  },

  // ----- Business data -----
  // Selector endpoints are unpaginated (bare arrays); tolerate an envelope too.
  async getOrganizations() {
    const response = await api.get('/api/organizations/');
    return response.data.results ?? response.data;
  },

  async getDataSources() {
    const response = await api.get('/api/datasources/');
    return response.data.results ?? response.data;
  },

  // Batches are paginated; return the current page as an array (recent-first).
  async getBatches(params = {}) {
    const response = await api.get('/api/batches/', { params });
    return response.data.results ?? response.data;
  },

  async getBatchDetail(batchId) {
    const response = await api.get(`/api/batches/${batchId}/`);
    return response.data;
  },

  // Lean job-lifecycle payload for polling (see useBatchProgress). Same
  // shape a future WebSocket/SSE push would send — deliberately not just
  // getBatchDetail() again, to keep frequent-polling payloads small.
  async getBatchProgress(batchId) {
    const response = await api.get(`/api/batches/${batchId}/progress/`);
    return response.data;
  },

  // Authenticated CSV export -> triggers a browser download (streamed server-side).
  async exportRecords(params = {}) {
    const response = await api.get('/api/records/export/', { params, responseType: 'blob' });
    const url = URL.createObjectURL(response.data);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'scopetrace_records.csv';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },

  /**
   * Fetch emission records (paginated). Returns { items, count, next, previous }.
   * @param {Object} params - filters + optional page / page_size
   */
  async getRecords(params = {}) {
    const response = await api.get('/api/records/', { params });
    const d = response.data;
    const items = d.results ?? d;
    return {
      items,
      count: d.count ?? items.length,
      next: d.next ?? null,
      previous: d.previous ?? null,
    };
  },

  /**
   * Upload an ESG file (CSV/JSON) based on its DataSource source type
   */
  async uploadFile(sourceType, file, dataSourceId, onUploadProgress) {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('data_source', dataSourceId);

    const response = await api.post(`/api/upload/${sourceType}/`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      onUploadProgress,
    });
    return response.data;
  },

  // Phase 6c governance workflow: Draft/Suspicious/Validated -> Submitted
  // -> Approved (terminal) or Submitted -> Rejected -> Submitted (resubmit).
  async submitRecord(recordId, reason = '') {
    const response = await api.post(`/api/records/${recordId}/submit/`, { reason });
    return response.data;
  },

  async approveRecord(recordId, reason = '') {
    const response = await api.post(`/api/records/${recordId}/approve/`, { reason });
    return response.data;
  },

  // Backend requires a non-empty reason for rejection.
  async rejectRecord(recordId, reason) {
    const response = await api.post(`/api/records/${recordId}/reject/`, { reason });
    return response.data;
  },

  // Phase 7b: read-only AI advisory annotations (e.g. anomaly explanations)
  // for a record -- never a mutation endpoint. Returns [] if none exist yet.
  async getRecordAIAnnotations(recordId) {
    const response = await api.get(`/api/records/${recordId}/ai-annotations/`);
    return response.data;
  },

  // Phase 7c: read-only AI advisory factor recommendations for a record --
  // never a mutation endpoint. Returns [] if none exist yet.
  async getRecordFactorRecommendations(recordId) {
    const response = await api.get(`/api/records/${recordId}/factor-recommendations/`);
    return response.data;
  },

  // ----- Metrics / analytics -----
  async getMetricsSummary(params = {}) {
    return (await api.get('/api/metrics/summary/', { params })).data;
  },
  async getMetricsTimeseries(params = {}) {
    return (await api.get('/api/metrics/timeseries/', { params })).data;
  },
  async getMetricsBreakdown(params = {}) {
    return (await api.get('/api/metrics/breakdown/', { params })).data;
  },
  async getActivityFeed() {
    return (await api.get('/api/metrics/activity/')).data;
  },
  async getPlatformMetrics() {
    return (await api.get('/api/metrics/platform/')).data;
  },
  async getFactorDatasets(params = {}) {
    const d = (await api.get('/api/factor-datasets/', { params })).data;
    return d.results ?? d;
  },
};

export default api;
