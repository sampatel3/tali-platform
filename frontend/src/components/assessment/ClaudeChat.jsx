import { useState, useRef, useEffect } from 'react';
import { Send, Bot } from 'lucide-react';

function formatUsd(value) {
  if (typeof value !== 'number' || Number.isNaN(value)) return 'N/A';
  return `$${value.toFixed(2)}`;
}

function formatInt(value) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '0';
  return value.toLocaleString();
}

export default function ClaudeChat({ onSendMessage, onPaste, disabled = false, budget = null, disabledReason = null }) {
  const [messages, setMessages] = useState([
    {
      role: 'assistant',
      content: "I'm here to help you debug. Ask me anything!",
    },
  ]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, loading]);

  const handleSend = async () => {
    const trimmed = input.trim();
    if (!trimmed || loading || disabled) return;

    const userMessage = { role: 'user', content: trimmed };
    const updatedMessages = [...messages, userMessage];
    setMessages(updatedMessages);
    setInput('');
    setLoading(true);

    try {
      // Build conversation history for the API (exclude the initial greeting)
      const history = updatedMessages
        .slice(1)
        .map((m) => ({ role: m.role, content: m.content }));

      const response = await onSendMessage(trimmed, history);
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: response },
      ]);
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: 'Sorry, something went wrong. Please try again.',
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const budgetEnabled = Boolean(budget?.enabled);
  const budgetExhausted = Boolean(budget?.is_exhausted);
  const headerBudgetText = budgetEnabled
    ? `Claude Credit left ${formatUsd(budget?.remaining_usd)} / ${formatUsd(budget?.limit_usd)}`
    : `Claude Credit used ${formatInt(budget?.tokens_used || 0)}`;
  const headerTokenEstimate = budgetEnabled && typeof budget?.remaining_total_tokens_estimate === 'number'
    ? `~${formatInt(budget.remaining_total_tokens_estimate)} credits left (est.)`
    : null;
  const inputPlaceholder =
    disabledReason === 'budget_exhausted'
      ? 'Claude budget exhausted for this task.'
      : disabledReason === 'timer_paused'
        ? 'Assessment is paused while Claude is unavailable.'
        : 'Ask Claude for help...';

  return (
    <div className="h-full flex flex-col bg-white">
      {/* Header */}
      <div className="border-b-2 border-black px-4 py-2">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Bot size={18} className="text-[var(--taali-purple)]" />
            <span className="font-mono text-sm font-bold">Claude AI</span>
          </div>
          <span className={`font-mono text-[11px] ${budgetExhausted ? 'text-red-700 font-bold' : 'text-gray-600'}`}>
            {headerBudgetText}
          </span>
        </div>
        {(budgetEnabled || budget?.tokens_used) && (
          <div className="mt-1 font-mono text-[11px] text-gray-500">
            Claude Credit used: {formatInt(budget?.tokens_used || 0)}
            {headerTokenEstimate ? ` • ${headerTokenEstimate}` : ''}
          </div>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`border-2 border-black px-3 py-2 max-w-[85%] font-mono text-sm whitespace-pre-wrap ${
                msg.role === 'assistant'
                  ? 'bg-purple-50'
                  : 'bg-gray-50'
              }`}
            >
              {msg.content}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="border-2 border-black px-3 py-2 bg-purple-50 font-mono text-sm animate-pulse">
              Claude is thinking...
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="border-t-2 border-black p-3 flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={onPaste}
          placeholder={inputPlaceholder}
          disabled={loading || disabled}
          className="flex-1 border-2 border-black px-3 py-2 font-mono text-sm focus:outline-none disabled:opacity-50"
        />
        <button
          onClick={handleSend}
          disabled={loading || !input.trim() || disabled}
          className="border-2 border-[var(--taali-border)] px-4 py-2 text-[var(--taali-surface)] font-bold flex items-center gap-1 bg-[var(--taali-purple)] hover:opacity-90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Send size={14} />
        </button>
      </div>
    </div>
  );
}
