import React, { useState } from 'react';
import ChatWindow from './components/Chat/ChatWindow';
import FileUpload from './components/Upload';
import { Database } from 'lucide-react';
import './App.css';

function App() {
  const [dataSourceId, setDataSourceId] = useState<string | undefined>(undefined);
  const [dataSourceName, setDataSourceName] = useState<string>('');

  const handleUploadSuccess = (fileId: string, filename: string) => {
    setDataSourceId(fileId);
    setDataSourceName(filename);
  };

  return (
    <div className="app">
      <header className="app-header">
        <h1>
          <Database size={24} />
          数据分析 Agent
        </h1>
      </header>

      <main className="app-main">
        <aside className="sidebar">
          <h2>数据源</h2>
          <FileUpload onUploadSuccess={handleUploadSuccess} />

          {dataSourceName && (
            <div className="current-datasource">
              <h3>当前数据源</h3>
              <p>{dataSourceName}</p>
            </div>
          )}
        </aside>

        <section className="chat-section">
          <ChatWindow dataSourceId={dataSourceId} />
        </section>
      </main>
    </div>
  );
}

export default App;