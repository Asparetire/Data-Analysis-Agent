import { useEffect } from 'react';
import { Database, FileText, FileSpreadsheet, CheckCircle2 } from 'lucide-react';
import { useChatStore } from '../../store/chatStore';
import { getDataSources } from '../../services/api';

const TYPE_LABEL: Record<string, string> = {
  csv: 'CSV',
  excel: 'XLSX',
  json: 'JSON',
};

function TypeIcon({ type }: { type: string }) {
  if (type === 'csv' || type === 'json') return <FileText size={16} />;
  if (type === 'excel') return <FileSpreadsheet size={16} />;
  return <FileText size={16} />;
}

export default function Sidebar() {
  const dataSources = useChatStore((s) => s.dataSources);
  const setDataSources = useChatStore((s) => s.setDataSources);
  const activeId = useChatStore((s) => s.activeDataSourceId);
  const activeName = useChatStore((s) => s.activeDataSourceName);
  const setActive = useChatStore((s) => s.setActiveDataSource);
  const uploadedFileName = useChatStore((s) => s.uploadedFileName);

  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      try {
        const list = await getDataSources();
        if (alive) setDataSources(list);
      } catch (e) {
        // Silent: sidebar is non-critical. Log only.
        // eslint-disable-next-line no-console
        console.warn('failed to load data sources', e);
      }
    };
    refresh();
    return () => {
      alive = false;
    };
  }, [setDataSources, uploadedFileName]);

  return (
    <aside className="sidebar">
      <h2>数据源</h2>
      {activeId && activeName ? (
        <div className="current-datasource">
          <span className="ds-icon">
            <Database size={16} />
          </span>
          <div className="ds-meta">
            <div className="ds-name" title={activeName}>
              {activeName}
            </div>
            <div className="ds-sub">
              <CheckCircle2 size={11} style={{ marginRight: 4, verticalAlign: -1 }} />
              当前使用中
            </div>
          </div>
        </div>
      ) : (
        <div className="current-datasource" style={{ background: 'var(--color-bg)' }}>
          <span className="ds-icon" style={{ background: 'var(--color-text-muted)' }}>
            <Database size={16} />
          </span>
          <div className="ds-meta">
            <div className="ds-name" style={{ color: 'var(--color-text-muted)' }}>
              未选择数据源
            </div>
            <div className="ds-sub">请先上传文件</div>
          </div>
        </div>
      )}

      <div>
        <h3>历史数据源</h3>
        {dataSources.length === 0 ? (
          <p style={{ fontSize: 12, color: 'var(--color-text-muted)', margin: 0 }}>
            暂无历史数据源
          </p>
        ) : (
          <div className="datasource-list">
            {dataSources.map((ds) => (
              <button
                key={ds.id}
                type="button"
                className={`datasource-item ${activeId === ds.id ? 'active' : ''}`}
                onClick={() => setActive({ id: ds.id, name: ds.name })}
                title={ds.name}
              >
                <TypeIcon type={ds.type} />
                <span className="ds-name">{ds.name}</span>
                <span className="ds-type">{TYPE_LABEL[ds.type] || ds.type}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}
