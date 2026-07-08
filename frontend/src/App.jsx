import { useEffect, useState } from 'react';
import DashboardPage from './pages/DashboardPage';
import UploadPage from './pages/UploadPage';
import RecordsPage from './pages/RecordsPage';
import LoginPage from './pages/LoginPage';
import ESGAssistantPage from './pages/ESGAssistantPage';
import { useAuth } from './context/AuthContext';

const ROLE_LABELS = {
  ORG_ADMIN: 'Organization Admin',
  ANALYST: 'ESG Analyst',
  AUDITOR: 'Auditor',
  VIEWER: 'Viewer',
};

function App() {
  const { isAuthenticated, loading, user, role, isPlatformAdmin, canUpload, canUseAI, logout } = useAuth();
  // Simple, ultra-stable state-based router
  const [view, setView] = useState({ name: 'dashboard', params: {} });
  const [menuOpen, setMenuOpen] = useState(false);

  // Redirect away from the Upload view if the role can't upload.
  useEffect(() => {
    if (view.name === 'upload' && !canUpload) {
      setView({ name: 'dashboard', params: {} });
    }
  }, [view.name, canUpload]);

  // Redirect away from the ESG Assistant view if the role can't use AI.
  useEffect(() => {
    if (view.name === 'esg-assistant' && !canUseAI) {
      setView({ name: 'dashboard', params: {} });
    }
  }, [view.name, canUseAI]);

  // While resolving the session, show a neutral splash.
  if (loading) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center">
        <svg className="animate-spin h-8 w-8 text-brand-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
        </svg>
      </div>
    );
  }

  // Protected: unauthenticated users only see the login page.
  if (!isAuthenticated) {
    return <LoginPage />;
  }

  const renderActiveView = () => {
    switch (view.name) {
      case 'dashboard':
        return <DashboardPage setView={setView} />;
      case 'upload':
        return canUpload ? <UploadPage setView={setView} /> : <DashboardPage setView={setView} />;
      case 'records':
        return <RecordsPage initialFilters={view.params} key={JSON.stringify(view.params)} />;
      case 'esg-assistant':
        return canUseAI ? <ESGAssistantPage /> : <DashboardPage setView={setView} />;
      default:
        return <DashboardPage setView={setView} />;
    }
  };

  const roleLabel = isPlatformAdmin ? 'Platform Admin' : ROLE_LABELS[role] || 'Member';
  const orgName = user?.active_organization?.name || (isPlatformAdmin ? 'All Organizations' : '—');
  const navBtn = (active) =>
    `flex items-center gap-3 px-4 py-3 rounded-xl text-xs font-black uppercase tracking-wider text-left transition-all duration-300 focus:outline-none ${
      active
        ? 'bg-brand-500/10 border border-brand-500/20 text-brand-400 shadow-[0_4px_15px_#2ebb720c]'
        : 'border border-transparent text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
    }`;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex font-sans antialiased selection:bg-brand-500/30 selection:text-brand-300">

      {/* Sidebar Navigation */}
      <aside className="w-[260px] bg-slate-900 border-r border-slate-800/80 flex flex-col justify-between p-6 select-none shrink-0">

        {/* Brand Banner */}
        <div className="flex flex-col gap-8">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-brand-500/10 border border-brand-500/20 text-brand-400 rounded-xl shadow-[0_0_15px_#2ebb7220]">
              <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
              </svg>
            </div>
            <div className="flex flex-col">
              <span className="font-extrabold text-white text-base tracking-tight font-sans">
                ScopeTrace
              </span>
              <span className="text-[10px] text-slate-500 font-bold uppercase tracking-widest leading-none">
                Carbon Accounting Platform
              </span>
            </div>
          </div>

          {/* Navigation Links */}
          <nav className="flex flex-col gap-1.5">
            <button onClick={() => setView({ name: 'dashboard', params: {} })} className={navBtn(view.name === 'dashboard')}>
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2H6a2 2 0 01-2-2v-4zM14 16a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2h-2a2 2 0 01-2-2v-4z" />
              </svg>
              Dashboard
            </button>

            {/* Upload — only for roles that can upload */}
            {canUpload && (
              <button onClick={() => setView({ name: 'upload', params: {} })} className={navBtn(view.name === 'upload')}>
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                </svg>
                Upload Center
              </button>
            )}

            <button onClick={() => setView({ name: 'records', params: {} })} className={navBtn(view.name === 'records')}>
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
              </svg>
              Review Ledger
            </button>

            {/* ESG Assistant — only for roles that can use AI */}
            {canUseAI && (
              <button onClick={() => setView({ name: 'esg-assistant', params: {} })} className={navBtn(view.name === 'esg-assistant')}>
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                </svg>
                ESG Assistant
              </button>
            )}
          </nav>
        </div>

        {/* User Profile Dropdown */}
        <div className="relative flex flex-col gap-2 border-t border-slate-800/60 pt-4">
          {menuOpen && (
            <div className="absolute bottom-full mb-2 left-0 right-0 bg-slate-950 border border-slate-800 rounded-lg shadow-2xl p-1.5 flex flex-col gap-1 z-10">
              <div className="px-3 py-2 flex flex-col gap-0.5">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Organization</span>
                <span className="text-xs font-semibold text-slate-300 truncate" title={orgName}>{orgName}</span>
              </div>
              <button
                onClick={async () => { setMenuOpen(false); await logout(); }}
                className="text-left px-3 py-2 rounded-md text-xs font-semibold text-rose-300 hover:bg-rose-950/30 transition-all focus:outline-none"
              >
                Sign out
              </button>
            </div>
          )}

          <button
            onClick={() => setMenuOpen((o) => !o)}
            className="flex items-center gap-3 p-2 rounded-lg hover:bg-slate-800/40 transition-all focus:outline-none text-left"
          >
            <span className="w-8 h-8 rounded-full bg-brand-500/15 border border-brand-500/30 text-brand-300 flex items-center justify-center text-xs font-bold uppercase shrink-0">
              {(user?.username || '?').slice(0, 2)}
            </span>
            <div className="flex flex-col min-w-0">
              <span className="text-xs font-bold text-slate-200 truncate">{user?.username}</span>
              <span className="text-[10px] text-brand-400 font-semibold uppercase tracking-wide truncate">{roleLabel}</span>
            </div>
            <svg className={`w-4 h-4 text-slate-500 ml-auto transition-transform ${menuOpen ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
            </svg>
          </button>
        </div>

      </aside>

      {/* Main Core Content Panel */}
      <main className="flex-1 p-8 overflow-y-auto max-w-7xl mx-auto w-full">
        {renderActiveView()}
      </main>

    </div>
  );
}

export default App;
