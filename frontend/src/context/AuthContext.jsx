import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { apiService, tokenStore, setAuthFailureHandler } from '../services/api';

const AuthContext = createContext(null);

// Roles permitted to upload / approve / use AI features (mirrors the
// backend RBAC matrix -- apps.accounts.permissions).
const UPLOAD_ROLES = ['ORG_ADMIN', 'ANALYST'];
const APPROVE_ROLES = ['ORG_ADMIN', 'ANALYST', 'AUDITOR'];
const AI_ROLES = ['ORG_ADMIN', 'ANALYST', 'AUDITOR'];

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadUser = useCallback(async () => {
    if (!tokenStore.getAccess()) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      const me = await apiService.getCurrentUser();
      setUser(me);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // If a token refresh fails irrecoverably, the API layer calls this to log out.
    setAuthFailureHandler(() => setUser(null));
    loadUser();
  }, [loadUser]);

  const login = async (username, password) => {
    await apiService.login(username, password);
    await loadUser();
  };

  const logout = async () => {
    await apiService.logout();
    setUser(null);
  };

  const role = user?.active_role || null;
  const isPlatformAdmin = !!user?.is_platform_admin;

  const value = {
    user,
    loading,
    isAuthenticated: !!user,
    role,
    isPlatformAdmin,
    canUpload: isPlatformAdmin || (role ? UPLOAD_ROLES.includes(role) : false),
    canApprove: isPlatformAdmin || (role ? APPROVE_ROLES.includes(role) : false),
    canUseAI: isPlatformAdmin || (role ? AI_ROLES.includes(role) : false),
    // Mirrors backend IsOrgAdmin (apps.accounts.permissions) exactly --
    // gates the ?deleted=true "trash" view on GET /api/records/, which is
    // an administrative-oversight capability, not a routine read.
    canViewDeletedRecords: isPlatformAdmin || role === 'ORG_ADMIN',
    // D5: true when the backend is running with DEMO_MODE=True (background
    // work executes synchronously in-process, no Celery worker/Beat) --
    // drives the Demo Mode banner in App.jsx. Absent/false in production.
    demoMode: !!user?.demo_mode,
    login,
    logout,
    refreshUser: loadUser,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = () => {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return ctx;
};

export default AuthContext;
