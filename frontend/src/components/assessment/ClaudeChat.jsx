import { useState, useRef, useEffect } from 'react';
import { Send, Bot } from 'lucide-react';

export default function ClaudeChat({ onSendMessage, onPaste, disabled = false }) {
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

  return (
    <div className="h-full flex flex-col bg-white">
      {/* Header */}
      <div className="border-b-2 border-black px-4 py-2 flex items-center gap-2">
        <Bot size={18} style={{ color: '#9D00FF' }} />
        <span className="font-mono text-sm font-bold">Claude AI</span>
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
          placeholder="Ask Claude for help..."
          disabled={loading || disabled}
          className="flex-1 border-2 border-black px-3 py-2 font-mono text-sm focus:outline-none disabled:opacity-50"
        />
        <button
          onClick={handleSend}
          disabled={loading || !input.trim() || disabled}
          className="border-2 border-black px-4 py-2 text-white font-bold flex items-center gap-1 hover:bg-black transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          style={{ backgroundColor: '#9D00FF' }}
        >
          <Send size={14} />
        </button>
      </div>
    </div>
  );
}
