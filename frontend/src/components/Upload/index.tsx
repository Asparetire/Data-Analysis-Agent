import { useCallback, useRef, type ChangeEvent, type DragEvent, type KeyboardEvent } from 'react';
import { Upload, FileText, CheckCircle, Loader2, AlertCircle, RotateCcw } from 'lucide-react';
import { useUpload } from '../../hooks/useUpload';
import './FileUpload.css';

interface FileUploadProps {
  /** Called after a successful upload so the parent can react (e.g. switch page). */
  onUploadSuccess?: (fileId: string, filename: string) => void;
}

export default function FileUpload({ onUploadSuccess }: FileUploadProps) {
  const { upload, reset, status, error, fileName } = useUpload();
  const inputRef = useRef<HTMLInputElement | null>(null);

  const handleFile = useCallback(
    async (file: File) => {
      const result = await upload(file);
      if (result && onUploadSuccess) onUploadSuccess(result.fileId, result.filename);
    },
    [upload, onUploadSuccess],
  );

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file);
  };

  const onChange = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
    // Allow the same file to be re-selected later.
    e.target.value = '';
  };

  const onClickArea = () => {
    if (status === 'idle' || status === 'error') {
      inputRef.current?.click();
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onClickArea();
    }
  };

  return (
    <div className="file-upload">
      <div
        className={`upload-area ${status === 'uploading' ? 'uploading' : ''} ${
          status === 'success' ? 'uploaded' : ''
        }`}
        onDragOver={(e) => e.preventDefault()}
        onDrop={onDrop}
        onClick={onClickArea}
        onKeyDown={onKeyDown}
        role="button"
        tabIndex={0}
        aria-label="Upload data file"
      >
        {status === 'uploading' ? (
          <div className="uploading">
            <Loader2 className="spin" size={28} />
            <p>上传中…</p>
          </div>
        ) : status === 'success' ? (
          <div className="uploaded">
            <CheckCircle size={28} />
            <p title={fileName ?? ''}>{fileName}</p>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                reset();
              }}
              style={{
                marginTop: 4,
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                fontSize: 12,
                background: 'transparent',
                border: '1px solid var(--color-border)',
                color: 'var(--color-text-muted)',
                padding: '4px 8px',
                borderRadius: 6,
              }}
            >
              <RotateCcw size={12} /> 重新上传
            </button>
          </div>
        ) : (
          <>
            <Upload size={32} color="var(--color-text-muted)" />
            <p>点击或拖拽文件到此处</p>
            <p className="supported-formats">支持 CSV / Excel / JSON (≤50MB)</p>
            <input
              ref={inputRef}
              type="file"
              accept=".csv,.xlsx,.xls,.json"
              onChange={onChange}
              className="file-input"
            />
          </>
        )}
      </div>

      {status === 'error' && error ? (
        <div className="upload-error" role="alert">
          <AlertCircle size={14} style={{ verticalAlign: -2, marginRight: 4 }} />
          {error}
        </div>
      ) : null}

      {status === 'idle' || status === 'error' ? (
        <div
          className="supported-formats"
          style={{ display: 'flex', alignItems: 'center', gap: 4 }}
        >
          <FileText size={12} /> 文件会被解析到独立 SQLite 数据库
        </div>
      ) : null}
    </div>
  );
}
