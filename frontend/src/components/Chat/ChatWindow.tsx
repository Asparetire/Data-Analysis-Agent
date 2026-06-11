import React, { useState, useRef, useEffect } from 'react';
import { useChat } from '../../hooks/useChat';
import { Send, Loader2 } from 'lucide-react';
import './ChatWindow.css';

interface ChatWindowProps {
  dataSourceId?: string;
}

const ChatWindow: React.FC<ChatWindowProps> = ({ dataSourceId }) => {
  const { messages, isLoading, sendMessage } = useChat();
  const [inputValue, setInputValue] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (inputValue.trim() && !isLoading) {
      sendMessage(inputValue.trim(), dataSourceId);
      setInputValue('');
    }
  };

  return (
    <div className="chat-window">
      <div className="chat-messages">
        {messages.map((msg, index) => (
          <div
            key={index}
            className={`message ${msg.role === 'user' ? 'user-message' : 'assistant-message'}`}
          >
            <div className="message-content">
              <p>{msg.content}</p>
              {msg.sqlQuery && (
                <pre className="sql-block">
                  <code>{msg.sqlQuery}</code>
                </pre>
              )}
              {msg.chartData && (
                <div className="chart-container">
                  {/* 渲染图表组件 */}
                  <pre>{JSON.stringify(msg.chartData, null, 2)}</pre>
                </div>
              )}
            </div>
          </div>
        ))}

        {isLoading && (
          <div className="message assistant-message">
            <div className="message-content">
              <Loader2 className="animate-spin" />
              <span>分析中...</span>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      <form onSubmit={handleSubmit} className="chat-input-form">
        <input
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          placeholder="输入你的数据分析问题..."
          disabled={isLoading}
        />
        <button type="submit" disabled={isLoading || !inputValue.trim()}>
          <Send size={20} />
        </button>
      </form>
    </div>
  );
};

export default ChatWindow;