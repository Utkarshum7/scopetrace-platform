import { useEffect, useRef, useState } from 'react';
import { apiService } from '../services/api';

/**
 * ESGAssistantPage — Phase 7e. A dedicated conversational page (not a
 * detail-drawer panel like AIInsightsPanel — a chat interface warrants its
 * own screen). Read-mostly: the one write action is asking a question,
 * which never mutates a governed ESG record, only the AI's own
 * conversation history (apps.ai.models.AIConversation/
 * AIConversationMessage). Every assistant response is clearly labeled
 * "AI Advisory" and shows its citations, retrieved context, and
 * confidence — nothing here can approve, reject, edit, or otherwise
 * change any record.
 */
const CONFIDENCE_STYLES = {
  LOW: 'bg-slate-800/60 border-slate-700 text-slate-400',
  MEDIUM: 'bg-amber-950/30 border-amber-500/30 text-amber-300',
  HIGH: 'bg-rose-950/30 border-rose-500/30 text-rose-300',
};

export const ESGAssistantPage = () => {
  const [conversations, setConversations] = useState([]);
  const [activeConversationId, setActiveConversationId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [question, setQuestion] = useState('');
  const [isLoadingConversations, setIsLoadingConversations] = useState(true);
  const [isAsking, setIsAsking] = useState(false);
  const [error, setError] = useState('');
  const bottomRef = useRef(null);

  const loadConversations = async () => {
    setIsLoadingConversations(true);
    try {
      const data = await apiService.listEsgConversations();
      setConversations(data);
    } catch {
      setConversations([]);
    } finally {
      setIsLoadingConversations(false);
    }
  };

  useEffect(() => {
    loadConversations();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: 'smooth' });
  }, [messages]);

  // Explicit, not effect-driven: selecting a conversation from the
  // sidebar fetches its real history. Deliberately NOT a useEffect keyed
  // on activeConversationId -- a brand-new conversation (from
  // startNewConversation/handleAsk) is known-empty and must set messages
  // to [] synchronously, or an in-flight history fetch for the PREVIOUS
  // conversation could resolve after and clobber an optimistic update.
  const selectConversation = async (conversationId) => {
    setError('');
    setActiveConversationId(conversationId);
    try {
      const data = await apiService.getEsgConversationMessages(conversationId);
      setMessages(data);
    } catch {
      setMessages([]);
    }
  };

  const startNewConversation = async () => {
    setError('');
    try {
      const conversation = await apiService.createEsgConversation();
      setConversations((prev) => [conversation, ...prev]);
      setActiveConversationId(conversation.id);
      setMessages([]);
      return conversation.id;
    } catch {
      setError('Could not start a new conversation.');
      return null;
    }
  };

  const handleAsk = async (e) => {
    e.preventDefault();
    const trimmed = question.trim();
    if (!trimmed) return;

    setError('');
    let conversationId = activeConversationId;
    if (!conversationId) {
      conversationId = await startNewConversation();
      if (!conversationId) return;
    }

    setMessages((prev) => [
      ...prev,
      { id: `pending-${Date.now()}`, role: 'USER', content: trimmed, created_at: new Date().toISOString() },
    ]);
    setQuestion('');
    setIsAsking(true);
    try {
      const result = await apiService.askEsgAssistant(conversationId, trimmed);
      if (result.assistant_message) {
        setMessages((prev) => [...prev, result.assistant_message]);
      } else {
        setError(result.detail || 'The assistant could not generate a response right now.');
      }
    } catch {
      setError('Something went wrong asking the assistant.');
    } finally {
      setIsAsking(false);
    }
  };

  return (
    <div className="flex gap-6 h-[calc(100vh-4rem)] animate-fadeIn">
      {/* Conversation list */}
      <aside className="w-64 shrink-0 flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-xl font-black text-white tracking-tight font-sans">ESG Assistant</h1>
          <p className="text-xs text-slate-400">Advisory only — never changes your data</p>
        </div>
        <button
          type="button"
          onClick={startNewConversation}
          className="px-3 py-2 rounded-lg bg-brand-500/10 border border-brand-500/20 text-brand-400 text-xs font-bold uppercase tracking-wider hover:bg-brand-500/20 transition-all focus:outline-none"
        >
          + New conversation
        </button>
        <div className="flex flex-col gap-1 overflow-y-auto">
          {!isLoadingConversations && conversations.length === 0 && (
            <p className="text-[11px] text-slate-500 px-2">No conversations yet.</p>
          )}
          {conversations.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => selectConversation(c.id)}
              className={`text-left px-3 py-2 rounded-lg text-xs font-semibold truncate transition-all focus:outline-none ${
                c.id === activeConversationId
                  ? 'bg-slate-800/60 text-slate-100 border border-slate-700'
                  : 'text-slate-400 hover:bg-slate-800/40 border border-transparent'
              }`}
            >
              {new Date(c.created_at).toLocaleString()}
            </button>
          ))}
        </div>
      </aside>

      {/* Conversation panel */}
      <section className="flex-1 flex flex-col rounded-lg border border-indigo-500/30 bg-indigo-950/20 overflow-hidden">
        <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-4">
          {messages.length === 0 && (
            <p className="text-xs text-slate-500 italic m-auto">
              Ask about uploaded datasets, emissions, calculations, scopes, factors, or platform usage.
            </p>
          )}
          {messages.map((m) => (
            <div key={m.id} className={`flex flex-col gap-1 max-w-2xl ${m.role === 'USER' ? 'self-end items-end' : 'self-start items-start'}`}>
              {m.role === 'ASSISTANT' && (
                <span className="px-1.5 py-0.5 rounded bg-indigo-500/20 border border-indigo-400/30 text-indigo-200 text-[9px] tracking-wide font-bold uppercase">
                  AI Advisory
                </span>
              )}
              <div
                className={`rounded-lg px-3 py-2 text-[12px] leading-relaxed ${
                  m.role === 'USER'
                    ? 'bg-brand-500/10 border border-brand-500/20 text-slate-100'
                    : 'bg-slate-900/60 border border-slate-800 text-slate-300'
                }`}
              >
                {m.content}
              </div>

              {m.role === 'ASSISTANT' && (
                <div className="flex flex-col gap-1.5 w-full">
                  {m.confidence && (
                    <span
                      className={`self-start px-1.5 py-0.5 rounded border text-[9px] font-bold uppercase tracking-wide ${
                        CONFIDENCE_STYLES[m.confidence] || CONFIDENCE_STYLES.LOW
                      }`}
                    >
                      {m.confidence} confidence
                    </span>
                  )}
                  {m.unsupported_claim && (
                    <span className="self-start px-1.5 py-0.5 rounded border border-amber-500/30 bg-amber-950/30 text-amber-300 text-[9px] font-bold uppercase tracking-wide">
                      Not fully supported by retrieved context
                    </span>
                  )}
                  {m.citations?.length > 0 && (
                    <div className="flex flex-col gap-0.5">
                      <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                        Citations
                      </span>
                      <ul className="list-disc list-inside space-y-0.5 text-[10px] text-slate-400 pl-1">
                        {m.citations.map((c, i) => (
                          <li key={i}>{c}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {m.retrieved_context && (
                    <details className="text-[10px] text-slate-500">
                      <summary className="cursor-pointer font-semibold uppercase tracking-wider">
                        Retrieved context
                      </summary>
                      <pre className="whitespace-pre-wrap mt-1 text-slate-400">{m.retrieved_context}</pre>
                    </details>
                  )}
                </div>
              )}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        {error && <p className="px-4 py-1 text-[11px] text-rose-400">{error}</p>}

        <form onSubmit={handleAsk} className="flex items-center gap-2 p-3 border-t border-indigo-500/10">
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="Ask about your emissions, scopes, factors, or platform usage…"
            className="flex-1 bg-slate-900/60 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 placeholder:text-slate-600 focus:outline-none focus:border-brand-500/40"
            disabled={isAsking}
          />
          <button
            type="submit"
            disabled={isAsking || !question.trim()}
            className="px-4 py-2 rounded-lg bg-brand-500/10 border border-brand-500/20 text-brand-400 text-xs font-bold uppercase tracking-wider hover:bg-brand-500/20 transition-all focus:outline-none disabled:opacity-40"
          >
            {isAsking ? 'Asking…' : 'Ask'}
          </button>
        </form>
      </section>
    </div>
  );
};

export default ESGAssistantPage;
