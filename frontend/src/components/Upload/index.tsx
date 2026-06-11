import React, { useState } from 'react';
import { Upload, FileText, CheckCircle, Loader2 } from 'lucide-react';
import { uploadFile } from '../../services/api';
import './FileUpload.css';

interface FileUploadProps {
  onUploadSuccess: (fileId: string, filename: string) => void;
}

const FileUpload: React.FC<FileUploadProps> = ({ onUploadSuccess }) => {
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadedFile, setUploadedFile] = useState<string | null>(null);

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
  };

  const handleFile = async (file: File) => {
    setIsUploading(true);
    try {
      const response = await uploadFile(file);
      setUploadedFile(response.filename);
      onUploadSuccess(response.file_id, response.filename);
    } catch (error) {
      console.error('Upload failed:', error);
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div className="file-upload">
      <div
        className={`upload-area ${isDragging ? 'dragging' : ''}`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {isUploading ? (
          <div className="uploading">
            <Loader2 className="animate-spin" />
            <p>上传中...</p>
          </div>
        ) : uploadedFile ? (
          <div className="uploaded">
            <CheckCircle className="text-green-500" />
            <p>{uploadedFile}</p>
          </div>
        ) : (
          <>
            <Upload size={48} className="text-gray-400" />
            <p>拖拽文件到此处，或点击选择文件</p>
            <p className="text-sm text-gray-500">支持 CSV、Excel 文件</p>
            <input
              type="file"
              accept=".csv,.xlsx,.xls"
              onChange={handleFileChange}
              className="file-input"
            />
          </>
        )}
      </div>
    </div>
  );
};

export default FileUpload;