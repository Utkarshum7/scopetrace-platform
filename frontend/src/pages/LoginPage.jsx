import { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { Spinner } from '../components/ui/Spinner';

export const LoginPage = () => {
  const { login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await login(username.trim(), password);
    } catch (err) {
      const status = err.response?.status;
      setError(
        status === 401
          ? 'Invalid username or password.'
          : 'Unable to sign in. Please try again.'
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex items-center justify-center p-4 font-sans antialiased">
      <div className="w-full max-w-sm flex flex-col gap-8">
        {/* Brand */}
        <div className="flex flex-col items-center gap-3">
          <div className="p-2.5 bg-brand-500/10 border border-brand-500/20 text-brand-400 rounded-xl shadow-[0_0_15px_#2ebb7220]">
            <svg className="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
            </svg>
          </div>
          <div className="flex flex-col items-center">
            <span className="font-extrabold text-white text-xl tracking-tight">ScopeTrace</span>
            <span className="text-[10px] text-slate-500 font-bold uppercase tracking-widest">
              Carbon Accounting Platform
            </span>
          </div>
        </div>

        {/* Card */}
        <form
          onSubmit={handleSubmit}
          className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-2xl flex flex-col gap-5"
        >
          <h1 className="text-sm font-bold text-white tracking-tight">Sign in to your workspace</h1>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="login-username" className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Username</label>
            <input
              id="login-username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
              aria-required="true"
              className="bg-slate-950 border border-slate-800 rounded-lg py-2.5 px-3 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition-all"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="login-password" className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Password</label>
            <input
              id="login-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
              aria-required="true"
              className="bg-slate-950 border border-slate-800 rounded-lg py-2.5 px-3 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition-all"
            />
          </div>

          {error && (
            <div role="alert" className="p-3 bg-rose-950/30 border border-rose-500/30 text-rose-300 text-xs rounded-lg animate-shake">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={isSubmitting}
            className="mt-1 px-5 py-2.5 bg-brand-600 hover:bg-brand-500 disabled:bg-slate-800 disabled:text-slate-600 text-white text-xs font-black uppercase tracking-wider rounded-lg transition-all shadow-md shadow-brand-600/10 flex items-center justify-center gap-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
          >
            {isSubmitting && <Spinner className="h-4 w-4 text-white" />}
            Sign In
          </button>
        </form>

        <p className="text-center text-[10px] text-slate-600 leading-relaxed">
          Demo users (password <span className="font-mono text-slate-500">demo12345</span>):
          <br />
          <span className="font-mono text-slate-500">orgadmin · analyst · auditor · viewer</span>
        </p>
      </div>
    </div>
  );
};

export default LoginPage;
