export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  chartData?: any;
  sqlQuery?: string;
}

export interface ChatResponse {
  session_id: string;
  message: string;
  chart_data?: any;
  sql_query?: string;
  error?: string;
}

export interface DataSource {
  id: string;
  name: string;
  type: 'csv' | 'excel' | 'database';
  created_at: string;
}

export interface UploadResponse {
  file_id: string;
  filename: string;
  status: string;
  message: string;
}