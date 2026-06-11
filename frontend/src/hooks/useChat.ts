import { useState, useCallback } from 'react';
import { ChatMessage, ChatResponse } from '../types';
import { sendChatMessage } from '../services/api';

export const useChat = () => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [sessionId] = useState(() => `session_${Date.now()}`);

  const sendMessage = useCallback(
    async (content: string, dataSourceId?: string) => {
      // 添加用户消息
      const userMessage: ChatMessage = {
        role: 'user',
        content,
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, userMessage]);
      setIsLoading(true);

      try {
        const response: ChatResponse = await sendChatMessage(
          sessionId,
          content,
          dataSourceId
        );

        const assistantMessage: ChatMessage = {
          role: 'assistant',
          content: response.message,
          timestamp: new Date().toISOString(),
          chartData: response.chart_data,
          sqlQuery: response.sql_query,
        };

        setMessages((prev) => [...prev, assistantMessage]);
      } catch (error) {
        const errorMessage: ChatMessage = {
          role: 'assistant',
          content: '抱歉，处理请求时发生错误，请稍后重试。',
          timestamp: new Date().toISOString(),
        };
        setMessages((prev) => [...prev, errorMessage]);
      } finally {
        setIsLoading(false);
      }
    },
    [sessionId]
  );

  const clearChat = useCallback(() => {
    setMessages([]);
  }, []);

  return {
    messages,
    isLoading,
    sendMessage,
    clearChat,
    sessionId,
  };
};