import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ESGAssistantPage } from './ESGAssistantPage';
import { apiService } from '../services/api';

// Phase 7e — contract-level tests: the assistant page is a live chat
// (asking is the one write action), never mutates a governed record, and
// every assistant response is clearly labeled "AI Advisory" with its
// citations/confidence/retrieved context.
vi.mock('../services/api', () => ({
  apiService: {
    listEsgConversations: vi.fn(),
    createEsgConversation: vi.fn(),
    getEsgConversationMessages: vi.fn(),
    askEsgAssistant: vi.fn(),
  },
}));

const conversation = { id: 'conv-1', created_at: '2026-07-08T12:00:00Z' };

const assistantMessage = {
  id: 'msg-2',
  role: 'ASSISTANT',
  content: 'Total CO2e was 842.15 tonnes.',
  citations: ['org_summary'],
  confidence: 'HIGH',
  unsupported_claim: false,
  retrieved_context: 'org_summary: total_co2e_tonnes=842.15',
  created_at: '2026-07-08T12:01:00Z',
};

describe('ESGAssistantPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiService.listEsgConversations.mockResolvedValue([]);
  });

  it('renders the empty state and AI Advisory framing when there are no conversations', async () => {
    render(<ESGAssistantPage />);
    expect(await screen.findByText('ESG Assistant')).toBeInTheDocument();
    expect(screen.getByText(/advisory only/i)).toBeInTheDocument();
    expect(screen.getByText(/no conversations yet/i)).toBeInTheDocument();
  });

  it('lists existing conversations', async () => {
    apiService.listEsgConversations.mockResolvedValue([conversation]);
    render(<ESGAssistantPage />);
    expect(await screen.findByText(new Date(conversation.created_at).toLocaleString())).toBeInTheDocument();
  });

  it('starts a new conversation and adds it to the sidebar', async () => {
    apiService.createEsgConversation.mockResolvedValue(conversation);
    const user = userEvent.setup();
    render(<ESGAssistantPage />);

    await user.click(await screen.findByRole('button', { name: /new conversation/i }));
    await waitFor(() => expect(apiService.createEsgConversation).toHaveBeenCalled());
    expect(await screen.findByText(new Date(conversation.created_at).toLocaleString())).toBeInTheDocument();
  });

  it('selecting an existing conversation from the sidebar fetches its real history', async () => {
    apiService.listEsgConversations.mockResolvedValue([conversation]);
    apiService.getEsgConversationMessages.mockResolvedValue([assistantMessage]);
    const user = userEvent.setup();
    render(<ESGAssistantPage />);

    await user.click(await screen.findByText(new Date(conversation.created_at).toLocaleString()));
    expect(await screen.findByText(assistantMessage.content)).toBeInTheDocument();
    expect(apiService.getEsgConversationMessages).toHaveBeenCalledWith('conv-1');
  });

  it('asking a question creates a conversation on demand, shows the question, and shows the labeled assistant answer with citations and confidence', async () => {
    apiService.createEsgConversation.mockResolvedValue(conversation);
    apiService.getEsgConversationMessages.mockResolvedValue([]);
    apiService.askEsgAssistant.mockResolvedValue({ assistant_message: assistantMessage });
    const user = userEvent.setup();
    render(<ESGAssistantPage />);

    const input = screen.getByPlaceholderText(/ask about your emissions/i);
    await user.type(input, 'What was our total CO2e?');
    await user.click(screen.getByRole('button', { name: /^ask$/i }));

    expect(await screen.findByText('What was our total CO2e?')).toBeInTheDocument();
    expect(await screen.findByText(assistantMessage.content)).toBeInTheDocument();
    expect(screen.getAllByText('AI Advisory').length).toBeGreaterThan(0);
    expect(screen.getByText(/HIGH confidence/i)).toBeInTheDocument();
    expect(screen.getByText('org_summary')).toBeInTheDocument();
    expect(apiService.askEsgAssistant).toHaveBeenCalledWith('conv-1', 'What was our total CO2e?');
  });

  it('shows a "Thinking" indicator while waiting for the assistant, and clears it once the answer arrives', async () => {
    apiService.createEsgConversation.mockResolvedValue(conversation);
    apiService.getEsgConversationMessages.mockResolvedValue([]);
    let resolveAsk;
    apiService.askEsgAssistant.mockReturnValue(new Promise((res) => { resolveAsk = res; }));
    const user = userEvent.setup();
    render(<ESGAssistantPage />);

    const input = screen.getByPlaceholderText(/ask about your emissions/i);
    await user.type(input, 'What was our total CO2e?');
    await user.click(screen.getByRole('button', { name: /^ask$/i }));

    expect(await screen.findByLabelText(/assistant is thinking/i)).toBeInTheDocument();
    resolveAsk({ assistant_message: assistantMessage });
    await screen.findByText(assistantMessage.content);
    expect(screen.queryByLabelText(/assistant is thinking/i)).not.toBeInTheDocument();
  });

  it('shows an error message instead of a fabricated answer when the assistant is unavailable', async () => {
    apiService.createEsgConversation.mockResolvedValue(conversation);
    apiService.getEsgConversationMessages.mockResolvedValue([]);
    apiService.askEsgAssistant.mockResolvedValue({
      assistant_message: null,
      detail: 'The assistant could not generate a response right now.',
    });
    const user = userEvent.setup();
    render(<ESGAssistantPage />);

    const input = screen.getByPlaceholderText(/ask about your emissions/i);
    await user.type(input, 'What was our total CO2e?');
    await user.click(screen.getByRole('button', { name: /^ask$/i }));

    expect(await screen.findByText(/could not generate a response/i)).toBeInTheDocument();
  });

  it('flags an unsupported claim distinctly from a normal confident answer', async () => {
    apiService.createEsgConversation.mockResolvedValue(conversation);
    apiService.getEsgConversationMessages.mockResolvedValue([]);
    apiService.askEsgAssistant.mockResolvedValue({
      assistant_message: { ...assistantMessage, unsupported_claim: true, confidence: 'LOW' },
    });
    const user = userEvent.setup();
    render(<ESGAssistantPage />);

    const input = screen.getByPlaceholderText(/ask about your emissions/i);
    await user.type(input, 'What was our Scope 3 total?');
    await user.click(screen.getByRole('button', { name: /^ask$/i }));

    expect(await screen.findByText(/not fully supported by retrieved context/i)).toBeInTheDocument();
  });
});
