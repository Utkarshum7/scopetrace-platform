import axios from 'axios';

// Get base URL from Vite environment variables, default to Django local development port
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const api = axios.create({
  baseURL: API_BASE_URL,
  // Fail requests after 60s instead of hanging forever. Generous enough to
  // tolerate a cold-start on free-tier hosting, but bounded.
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Response interceptor for clean error handling
api.interceptors.response.use(
  (response) => response,
  (error) => {
    console.error('API Error:', error.response?.data || error.message);
    return Promise.reject(error);
  }
);

// Ingestion API Helper Wrappers
export const apiService = {
  /**
   * Fetch all Organization tenants
   */
  async getOrganizations() {
    const response = await api.get('/api/organizations/');
    return response.data;
  },

  /**
   * Fetch all registered DataSources
   */
  async getDataSources() {
    const response = await api.get('/api/datasources/');
    return response.data;
  },

  /**
   * Fetch list of all upload batches
   */
  async getBatches() {
    const response = await api.get('/api/batches/');
    return response.data;
  },

  /**
   * Fetch details for a specific upload batch
   */
  async getBatchDetail(batchId) {
    const response = await api.get(`/api/batches/${batchId}/`);
    return response.data;
  },

  /**
   * Fetch emission records with active query filters
   * @param {Object} params - Query parameters (organization, data_source, batch, suspicious, failed, status)
   */
  async getRecords(params = {}) {
    const response = await api.get('/api/records/', { params });
    return response.data;
  },

  /**
   * Upload an ESG file (CSV/JSON) based on its DataSource source type
   * @param {string} sourceType - 'sap', 'utility', or 'travel'
   * @param {File} file - The file object
   * @param {string} dataSourceId - The DataSource UUID
   * @param {Function} [onUploadProgress] - Optional progress callback
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

  /**
   * Transactional Analyst Approval action with optional reasoning
   * @param {string} recordId - The EmissionRecord UUID
   * @param {string} [reason] - Optional analyst rationale
   */
  async approveRecord(recordId, reason = '') {
    const response = await api.post(`/api/records/${recordId}/approve/`, { reason });
    return response.data;
  },
};

export default api;
