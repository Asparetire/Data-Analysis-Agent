import { useCallback } from 'react';
import axios from 'axios';
import { uploadFile } from '../services/api';
import { useChatStore } from '../store/chatStore';
import { validateFile } from '../utils';

interface UploadResult {
  fileId: string;
  filename: string;
}

export function useUpload() {
  const status = useChatStore((s) => s.uploadStatus);
  const error = useChatStore((s) => s.uploadError);
  const fileName = useChatStore((s) => s.uploadedFileName);
  const setStatus = useChatStore((s) => s.setUploadStatus);
  const setError = useChatStore((s) => s.setUploadError);
  const setFileName = useChatStore((s) => s.setUploadedFileName);
  const setActive = useChatStore((s) => s.setActiveDataSource);

  const reset = useCallback(() => {
    setStatus('idle');
    setError(null);
    setFileName(null);
  }, [setStatus, setError, setFileName]);

  const upload = useCallback(
    async (file: File): Promise<UploadResult | null> => {
      const validation = validateFile(file);
      if (validation) {
        setStatus('error');
        setError(validation);
        return null;
      }

      setStatus('uploading');
      setError(null);

      try {
        const res = await uploadFile(file);
        setStatus('success');
        setFileName(res.filename);
        setActive({ id: res.file_id, name: res.filename });
        return { fileId: res.file_id, filename: res.filename };
      } catch (err) {
        const detail = axios.isAxiosError(err)
          ? err.response?.data?.detail || err.message
          : (err as Error).message;
        setStatus('error');
        setError(detail || 'Upload failed');
        return null;
      }
    },
    [setStatus, setError, setFileName, setActive],
  );

  return { upload, reset, status, error, fileName };
}
