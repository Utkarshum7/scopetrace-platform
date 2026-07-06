import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { apiService, tokenStore, setAuthFailureHandler } from '../services/api';

const AuthContext = createContext(null);

// Roles permitted to upload / approve (mirrors the backend RBAC matrix).
const UPLOAD_ROLES = ['ORG_ADMIN', 'ANALYST'];
const APPROVE_ROLES = ['ORG_ADMIN', 'ANALYST', 'AUDITOR'];

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
