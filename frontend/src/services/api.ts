import axios from 'axios';

const API_BASE_URL = 'http://localhost:8000/api/v1';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

export const uploadFile = async (file: File) => {
  const formData = new FormData();
  formData.append('file', file);

  const response = await api.post<UploadResponse>('/upload', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });

  return response.data;
};

export const sendChatMessage = async (
  sessionId: string,
  message: string,
  dataSourceId?: string
) => {
  const response = await api.post<ChatResponse>('/chat', {
    session_id: sessionId,
    message,
    data_source_id: dataSourceId,
  });

  return response.data;
};

export const getDataSources = async () => {
  const response = await api.get<DataSource[]>('/datasources');
  return response.data;
};

export default api;