import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// Phase 7.5 (H3) -- AuthContext is the single RBAC-derivation point every
// role-gated page/widget in the app reads from (canUpload/canApprove/
// canUseAI/isPlatformAdmin). These tests pin: role -> permission-flag
// derivation for every role in the backend's matrix, session bootstrap from
// a stored token, login/logout state transitions, and the auth-failure
// (refresh-exhausted) forced-logout path -- the exact wiring api.js's
// interceptor depends on (setAuthFailureHandler).
vi.mock('../services/api', () => ({
  apiService: {
    login: vi.fn(),
    logout: vi.fn(),
    getCurrentUser: vi.fn(),
  },
  tokenStore: {
    getAccess: vi.fn(),
    getRefresh: vi.fn(),
    set: vi.fn(),
    clear: vi.fn(),
  },
  setAuthFailureHandler: vi.fn(),
}));

import { apiService, tokenStore, setAuthFailureHandler } from '../services/api';
import { AuthProvider, useAuth } from './AuthContext';

function Probe() {
  const auth = useAuth();
  if (auth.loading) return <div>loading</div>;
  return (
    <div>
      <div data-testid="authenticated">{String(auth.isAuthenticated)}</div>
      <div data-testid="role">{auth.role ?? 'none'}</div>
      <div data-testid="platform-admin">{String(auth.isPlatformAdmin)}</div>
      <div data-testid="can-upload">{String(auth.canUpload)}</div>
      <div data-testid="can-approve">{String(auth.canApprove)}</div>
      <div data-testid="can-use-ai">{String(auth.canUseAI)}</div>
      <button onClick={() => auth.login('u', 'p')}>login</button>
      <button onClick={() => auth.logout()}>logout</button>
    </div>
  );
}

function renderProbe() {
  return render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  );
}

describe('AuthContext', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    tokenStore.getAccess.mockReturnValue(null);
  });

  describe('session bootstrap', () => {
    it('resolves unauthenticated with no stored token, without calling the API', async () => {
      renderProbe();
      await waitFor(() => expect(screen.getByTestId('authenticated')).toHaveTextContent('false'));
      expect(apiService.getCurrentUser).not.toHaveBeenCalled();
    });

    it('resolves authenticated from a stored token via getCurrentUser', async () => {
      tokenStore.getAccess.mockReturnValue('stored-token');
      apiService.getCurrentUser.mockResolvedValue({ username: 'alice', active_role: 'ANALYST' });
      renderProbe();
      await waitFor(() => expect(screen.getByTestId('authenticated')).toHaveTextContent('true'));
      expect(screen.getByTestId('role')).toHaveTextContent('ANALYST');
    });

    it('treats a stored token that getCurrentUser rejects as unauthenticated (never crashes)', async () => {
      tokenStore.getAccess.mockReturnValue('stale-token');
      apiService.getCurrentUser.mockRejectedValue({ response: { status: 401 } });
      renderProbe();
      await waitFor(() => expect(screen.getByTestId('authenticated')).toHaveTextContent('false'));
    });

    it('registers the auth-failure handler so api.js can force a logout on an exhausted refresh', async () => {
      renderProbe();
      expect(setAuthFailureHandler).toHaveBeenCalledWith(expect.any(Function));
      // Let the initial loadUser() effect settle before the test tears down,
      // so its state update isn't left dangling outside of act().
      await waitFor(() => expect(screen.getByTestId('authenticated')).toHaveTextContent('false'));
    });

    it('the registered auth-failure handler clears the authenticated user', async () => {
      tokenStore.getAccess.mockReturnValue('stored-token');
      apiService.getCurrentUser.mockResolvedValue({ username: 'alice', active_role: 'ANALYST' });
      renderProbe();
      await waitFor(() => expect(screen.getByTestId('authenticated')).toHaveTextContent('true'));

      const registeredHandler = setAuthFailureHandler.mock.calls[0][0];
      registeredHandler();
      await waitFor(() => expect(screen.getByTestId('authenticated')).toHaveTextContent('false'));
    });
  });

  describe('login / logout', () => {
    it('login calls apiService.login then reloads the user', async () => {
      // The real apiService.login() stores the token as a side effect (see
      // api.js) before loadUser() re-checks tokenStore.getAccess(); mimic
      // that here since apiService is fully mocked.
      apiService.login.mockImplementation(async () => {
        tokenStore.getAccess.mockReturnValue('new-token');
        return {};
      });
      apiService.getCurrentUser.mockResolvedValue({ username: 'bob', active_role: 'ORG_ADMIN' });
      const user = userEvent.setup();
      renderProbe();
      await waitFor(() => expect(screen.getByTestId('authenticated')).toHaveTextContent('false'));

      await user.click(screen.getByText('login'));
      expect(apiService.login).toHaveBeenCalledWith('u', 'p');
      await waitFor(() => expect(screen.getByTestId('authenticated')).toHaveTextContent('true'));
      expect(screen.getByTestId('role')).toHaveTextContent('ORG_ADMIN');
    });

    it('logout calls apiService.logout and clears the user without a re-fetch', async () => {
      tokenStore.getAccess.mockReturnValue('stored-token');
      apiService.getCurrentUser.mockResolvedValue({ username: 'bob', active_role: 'ORG_ADMIN' });
      apiService.logout.mockResolvedValue(undefined);
      const user = userEvent.setup();
      renderProbe();
      await waitFor(() => expect(screen.getByTestId('authenticated')).toHaveTextContent('true'));

      await user.click(screen.getByText('logout'));
      expect(apiService.logout).toHaveBeenCalled();
      await waitFor(() => expect(screen.getByTestId('authenticated')).toHaveTextContent('false'));
      // logout must not re-hit getCurrentUser (that would re-authenticate a
      // session that was just explicitly ended).
      expect(apiService.getCurrentUser).toHaveBeenCalledTimes(1);
    });
  });

  describe('RBAC permission-flag derivation (mirrors apps.accounts.permissions)', () => {
    const asUser = (active_role, is_platform_admin = false) => ({ username: 'x', active_role, is_platform_admin });

    it.each([
      ['ORG_ADMIN', { upload: true, approve: true, ai: true }],
      ['ANALYST', { upload: true, approve: true, ai: true }],
      ['AUDITOR', { upload: false, approve: true, ai: true }],
      ['VIEWER', { upload: false, approve: false, ai: false }],
    ])('role %s derives canUpload/canApprove/canUseAI = %o', async (role, expected) => {
      tokenStore.getAccess.mockReturnValue('t');
      apiService.getCurrentUser.mockResolvedValue(asUser(role));
      renderProbe();
      await waitFor(() => expect(screen.getByTestId('role')).toHaveTextContent(role));

      expect(screen.getByTestId('can-upload')).toHaveTextContent(String(expected.upload));
      expect(screen.getByTestId('can-approve')).toHaveTextContent(String(expected.approve));
      expect(screen.getByTestId('can-use-ai')).toHaveTextContent(String(expected.ai));
    });

    it('a platform admin gets every permission regardless of active_role', async () => {
      tokenStore.getAccess.mockReturnValue('t');
      apiService.getCurrentUser.mockResolvedValue(asUser(null, true));
      renderProbe();
      await waitFor(() => expect(screen.getByTestId('platform-admin')).toHaveTextContent('true'));

      expect(screen.getByTestId('can-upload')).toHaveTextContent('true');
      expect(screen.getByTestId('can-approve')).toHaveTextContent('true');
      expect(screen.getByTestId('can-use-ai')).toHaveTextContent('true');
    });

    it('no role and no user yields every permission false, not a crash', async () => {
      renderProbe();
      await waitFor(() => expect(screen.getByTestId('authenticated')).toHaveTextContent('false'));
      expect(screen.getByTestId('can-upload')).toHaveTextContent('false');
      expect(screen.getByTestId('can-approve')).toHaveTextContent('false');
      expect(screen.getByTestId('can-use-ai')).toHaveTextContent('false');
    });
  });

  it('useAuth throws when used outside an AuthProvider', () => {
    const BadProbe = () => {
      useAuth();
      return null;
    };
    // Suppress React's expected console.error for this thrown-render case.
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => render(<BadProbe />)).toThrow('useAuth must be used within an AuthProvider');
    spy.mockRestore();
  });
});
