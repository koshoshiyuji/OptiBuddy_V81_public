import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';

interface DslDefinition {
  id: number;
  problem_class: string;
  version: string;
  extensions: string[];
  schema_json: Record<string, unknown>;
  description: string;
  created_at: string;
  updated_at: string;
}

interface Extension {
  id: number;
  name: string;
  category: string;
  applicable_domains: string[];
  description: string;
  created_at: string;
  updated_at: string;
}

interface EvolutionLog {
  id: number;
  dsl_name: string;
  extension_name: string | null;
  change_type: string;
  trigger_type: string;
  business_context: string | null;
  before_summary: string | null;
  after_summary: string | null;
  created_by: string;
  created_at: string;
}

interface RegistryData {
  dsl_definitions: DslDefinition[];
  extensions: Extension[];
  evolution_logs: EvolutionLog[];
  solvers: Array<{ name: string; description: string; is_default: boolean }>;
  overview: {
    dsl_definitions_count: number;
    extensions_count: number;
    evolution_logs_count: number;
    problem_classes: string[];
  };
}

interface RegistryViewProps {
  onClose: () => void;
  apiBase?: string;
}

export function RegistryView({ onClose, apiBase = "http://localhost:5000" }: RegistryViewProps) {
  const { t } = useTranslation();
  const [data, setData] = useState<RegistryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'overview' | 'dsl' | 'extensions' | 'solvers' | 'logs'>('overview');
  const [searchTerm, setSearchTerm] = useState('');

  // ドメイン削除用のstate
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deletingDomain, setDeletingDomain] = useState<string | null>(null);
  const [deletePreview, setDeletePreview] = useState<{
    scenarios: Array<{ id: number; name: string; domain: string }>;
    dsl_definitions: Array<{ id: number; problem_class: string; version: string }>;
    evolution_logs: Array<{ id: number; dsl_id: number }>;
    files: string[];
  } | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const fetchRegistry = async () => {
    try {
      setLoading(true);
      const res = await fetch(`${apiBase}/dsl_repository/registry`);
      const result = await res.json();
      if (result.status === 'ok') {
        setData(result.data);
      } else {
        setError(result.message || t('registryView.fetchFailed'));
      }
    } catch (e) {
      setError(`${t('registryView.connectionError')}: ${e}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchRegistry();
  }, [apiBase]);

  // ドメイン削除プレビュー
  const handleDeleteDomain = async (problemClass: string) => {
    setDeleteError(null);
    setDeleteLoading(true);
    setDeletingDomain(problemClass);
    
    try {
      const response = await fetch(
        `${apiBase}/dsl_repository/domain/${encodeURIComponent(problemClass)}?preview=true`,
        { method: 'DELETE' }
      );
      const result = await response.json();
      
      if (result.status !== 'ok') {
        setDeleteError(result.message || t('registryView.previewFailed'));
        setDeletingDomain(null);
      } else {
        setDeletePreview(result.targets);
        setShowDeleteConfirm(true);
      }
    } catch (e) {
      setDeleteError(`${t('registryView.connectionError')}: ${e}`);
      setDeletingDomain(null);
    } finally {
      setDeleteLoading(false);
    }
  };

  // ドメイン削除実行
  const confirmDelete = async () => {
    if (!deletingDomain) return;
    
    setDeleteError(null);
    setDeleteLoading(true);
    
    try {
      const response = await fetch(
        `${apiBase}/dsl_repository/domain/${encodeURIComponent(deletingDomain)}`,
        { method: 'DELETE' }
      );
      const result = await response.json();
      
      if (result.status !== 'ok') {
        setDeleteError(result.message || t('registryView.deleteFailed'));
      } else {
        // 削除成功: レジストリーを再読み込み
        setShowDeleteConfirm(false);
        setDeletingDomain(null);
        setDeletePreview(null);
        fetchRegistry();
      }
    } catch (e) {
      setDeleteError(`${t('registryView.connectionError')}: ${e}`);
    } finally {
      setDeleteLoading(false);
    }
  };

  // 削除キャンセル
  const cancelDelete = () => {
    setShowDeleteConfirm(false);
    setDeletingDomain(null);
    setDeletePreview(null);
    setDeleteError(null);
  };

  const filterBySearch = <T extends { name?: string; problem_class?: string; description?: string }>(items: T[]): T[] => {
    if (!searchTerm) return items;
    const term = searchTerm.toLowerCase();
    return items.filter(item => 
      (item.name?.toLowerCase().includes(term)) ||
      (item.problem_class?.toLowerCase().includes(term)) ||
      (item.description?.toLowerCase().includes(term))
    );
  };

  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, width: '100vw', height: '100vh',
      background: 'rgba(0,0,0,0.85)', display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 9999, fontFamily: 'monospace',
    }}>
      <div style={{
        background: '#121216', border: '1px solid #2a2a30', borderRadius: '12px',
        width: '90vw', maxWidth: '1400px', height: '85vh', display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
      }}>
        {/* ヘッダー */}
        <div style={{
          padding: '20px 24px', borderBottom: '1px solid #2a2a30',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <div>
            <h2 style={{ margin: 0, color: '#fff', fontSize: '20px', fontWeight: 900 }}>
              📚 {t('registryView.title')}
            </h2>
            <p style={{ margin: '4px 0 0 0', color: '#888', fontSize: '12px' }}>
              {t('registryView.subtitle')}
            </p>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'transparent', border: '1px solid #444', borderRadius: '6px',
              color: '#ccc', padding: '8px 16px', cursor: 'pointer', fontSize: '14px',
            }}
          >
            ✕ {t('registryView.close')}
          </button>
        </div>

        {loading && (
          <div style={{ padding: '40px', textAlign: 'center', color: '#888' }}>
            {t('registryView.loading')}
          </div>
        )}

        {error && (
          <div style={{ padding: '40px', textAlign: 'center', color: '#ff4444' }}>
            ⚠️ {error}
          </div>
        )}

        {data && (
          <>
            {/* タブナビゲーション */}
            <div style={{
              display: 'flex', gap: '8px', padding: '16px 24px', borderBottom: '1px solid #2a2a30',
              overflowX: 'auto',
            }}>
              {[
                { key: 'overview', label: `📊 ${t('registryView.tabOverview')}`, count: null },
                { key: 'dsl', label: `📋 ${t('registryView.tabDsl')}`, count: data.overview.dsl_definitions_count },
                { key: 'extensions', label: '🔧 Extensions', count: data.overview.extensions_count },
                { key: 'solvers', label: '⚙️ Solvers', count: data.solvers.length },
                { key: 'logs', label: `📈 ${t('registryView.tabLogs')}`, count: data.overview.evolution_logs_count },
              ].map(tab => (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key as 'overview' | 'dsl' | 'extensions' | 'solvers' | 'logs')}
                  style={{
                    background: activeTab === tab.key ? '#1e1e24' : 'transparent',
                    border: `1px solid ${activeTab === tab.key ? '#00e5ff' : '#333'}`,
                    borderRadius: '6px', padding: '8px 16px', cursor: 'pointer',
                    color: activeTab === tab.key ? '#00e5ff' : '#888',
                    fontSize: '13px', fontWeight: 700, whiteSpace: 'nowrap',
                  }}
                >
                  {tab.label} {tab.count !== null && `(${tab.count})`}
                </button>
              ))}
            </div>

            {/* 検索バー（概要以外） */}
            {activeTab !== 'overview' && (
              <div style={{ padding: '16px 24px', borderBottom: '1px solid #2a2a30' }}>
                <input
                  type="text"
                  placeholder={`🔍 ${t('registryView.search')}`}
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  style={{
                    width: '100%', padding: '10px 14px', background: '#1e1e24',
                    border: '1px solid #333', borderRadius: '6px', color: '#fff',
                    fontSize: '13px', fontFamily: 'monospace',
                  }}
                />
              </div>
            )}

            {/* コンテンツエリア */}
            <div style={{ flex: 1, overflow: 'auto', padding: '24px' }}>
              {activeTab === 'overview' && <OverviewTab data={data} />}
              {activeTab === 'dsl' && <DslTab definitions={filterBySearch(data.dsl_definitions)} onDelete={handleDeleteDomain} deleteLoading={deleteLoading} />}
              {activeTab === 'extensions' && <ExtensionsTab extensions={filterBySearch(data.extensions)} />}
              {activeTab === 'solvers' && <SolversTab solvers={data.solvers} />}
              {activeTab === 'logs' && <LogsTab logs={data.evolution_logs} searchTerm={searchTerm} />}
            </div>
          </>
        )}

        {/* 削除確認ダイアログ */}
        {showDeleteConfirm && deletePreview && (
          <div style={{
            position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
            background: 'rgba(0, 0, 0, 0.8)', display: 'flex',
            alignItems: 'center', justifyContent: 'center', zIndex: 10000,
          }}>
            <div style={{
              background: '#1a1a22', borderRadius: '16px', padding: '24px',
              maxWidth: '600px', width: '90%', maxHeight: '80vh', overflowY: 'auto',
              border: '2px solid #ff4444',
            }}>
              <div style={{ fontSize: '16px', fontWeight: 900, color: '#ff4444', marginBottom: '16px' }}>
                ⚠️ {t('registryView.confirmDeleteTitle')}
              </div>

              <div style={{ fontSize: '13px', color: '#ddd', marginBottom: '16px' }}>
                {t('registryView.confirmDeleteBody')}
              </div>
              
              <div style={{ background: '#0f0f18', borderRadius: '10px', padding: '16px', marginBottom: '16px' }}>
                <div style={{ fontSize: '14px', fontWeight: 900, color: '#00e5ff', marginBottom: '12px' }}>
                  {deletingDomain}
                </div>
                
                <div style={{ display: 'grid', gap: '12px' }}>
                  <div>
                    <div style={{ fontSize: '11px', color: '#888', marginBottom: '4px' }}>{t('registryView.scenarios')}</div>
                    <div style={{ fontSize: '13px', color: '#fff' }}>{t('registryView.countUnit', { count: deletePreview.scenarios.length })}</div>
                    {deletePreview.scenarios.length > 0 && (
                      <div style={{ marginTop: '6px', fontSize: '10px', color: '#aaa' }}>
                        {deletePreview.scenarios.map(s => s.name).join(', ')}
                      </div>
                    )}
                  </div>

                  <div>
                    <div style={{ fontSize: '11px', color: '#888', marginBottom: '4px' }}>{t('registryView.dslDefinitions')}</div>
                    <div style={{ fontSize: '13px', color: '#fff' }}>{t('registryView.countUnit', { count: deletePreview.dsl_definitions.length })}</div>
                  </div>

                  <div>
                    <div style={{ fontSize: '11px', color: '#888', marginBottom: '4px' }}>{t('registryView.evolutionLogs')}</div>
                    <div style={{ fontSize: '13px', color: '#fff' }}>{t('registryView.countUnit', { count: deletePreview.evolution_logs.length })}</div>
                  </div>

                  <div>
                    <div style={{ fontSize: '11px', color: '#888', marginBottom: '4px' }}>{t('registryView.files')}</div>
                    <div style={{ fontSize: '13px', color: '#fff' }}>{t('registryView.countUnit', { count: deletePreview.files.length })}</div>
                    {deletePreview.files.length > 0 && (
                      <div style={{ marginTop: '6px', fontSize: '10px', color: '#aaa', maxHeight: '100px', overflowY: 'auto' }}>
                        {deletePreview.files.map((f, i) => <div key={i}>{f}</div>)}
                      </div>
                    )}
                  </div>
                </div>
              </div>
              
              <div style={{ fontSize: '12px', color: '#ff6b6b', marginBottom: '20px', padding: '12px', background: '#2a1a1a', borderRadius: '8px' }}>
                ⚠️ {t('registryView.confirmDeleteWarning')}
              </div>
              
              {deleteError && (
                <div style={{ fontSize: '12px', color: '#ff4444', marginBottom: '16px', padding: '12px', background: '#2a1a1a', borderRadius: '8px' }}>
                  {deleteError}
                </div>
              )}
              
              <div style={{ display: 'flex', gap: '12px', justifyContent: 'flex-end' }}>
                <button
                  onClick={cancelDelete}
                  disabled={deleteLoading}
                  style={{
                    background: 'transparent', border: '1px solid #2a2a30', borderRadius: '10px',
                    padding: '12px 20px', cursor: deleteLoading ? 'not-allowed' : 'pointer',
                    color: '#ccc', fontWeight: 700, fontSize: '12px',
                    opacity: deleteLoading ? 0.5 : 1,
                  }}
                >
                  {t('registryView.cancel')}
                </button>
                <button
                  onClick={confirmDelete}
                  disabled={deleteLoading}
                  style={{
                    background: '#ff4444', border: 'none', borderRadius: '10px',
                    padding: '12px 20px', cursor: deleteLoading ? 'not-allowed' : 'pointer',
                    color: '#fff', fontWeight: 700, fontSize: '12px',
                    opacity: deleteLoading ? 0.5 : 1,
                  }}
                >
                  {deleteLoading ? t('registryView.deleting') : t('registryView.delete')}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// 概要タブ
function OverviewTab({ data }: { data: RegistryData }) {
  const { t } = useTranslation();
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
        gap: '16px',
      }}>
        {[
          { label: t('registryView.tabDsl'), value: data.overview.dsl_definitions_count, color: '#00e5ff' },
          { label: 'Extensions', value: data.overview.extensions_count, color: '#a78bfa' },
          { label: 'Solvers', value: data.solvers.length, color: '#1D9E75' },
          { label: t('registryView.tabLogs'), value: data.overview.evolution_logs_count, color: '#ff9800' },
        ].map(stat => (
          <div key={stat.label} style={{
            background: '#1e1e24', border: '1px solid #2a2a30', borderRadius: '8px',
            padding: '20px', textAlign: 'center',
          }}>
            <div style={{ fontSize: '32px', fontWeight: 900, color: stat.color }}>
              {stat.value}
            </div>
            <div style={{ fontSize: '12px', color: '#888', marginTop: '8px' }}>
              {stat.label}
            </div>
          </div>
        ))}
      </div>

      <div style={{
        background: '#1e1e24', border: '1px solid #2a2a30', borderRadius: '8px',
        padding: '20px',
      }}>
        <h3 style={{ margin: '0 0 16px 0', color: '#fff', fontSize: '16px' }}>
          Problem Classes
        </h3>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
          {data.overview.problem_classes.map(pc => (
            <span key={pc} style={{
              background: '#00e5ff22', border: '1px solid #00e5ff44',
              borderRadius: '4px', padding: '6px 12px', color: '#00e5ff',
              fontSize: '12px', fontWeight: 700,
            }}>
              {pc}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

// DSL定義タブ
function DslTab({ 
  definitions, 
  onDelete, 
  deleteLoading 
}: { 
  definitions: DslDefinition[]; 
  onDelete: (problemClass: string) => void;
  deleteLoading: boolean;
}) {
  const { t, i18n } = useTranslation();
  const [expandedId, setExpandedId] = useState<number | null>(null);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
      {definitions.length === 0 && (
        <div style={{ color: '#888', textAlign: 'center', padding: '40px' }}>
          {t('registryView.noDslDefinitions')}
        </div>
      )}
      {definitions.map(dsl => (
        <div key={dsl.id} style={{
          background: '#1e1e24', border: '1px solid #2a2a30', borderRadius: '8px',
          padding: '16px', display: 'flex', flexDirection: 'column', gap: '8px',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: '16px', fontWeight: 900, color: '#00e5ff' }}>
                {dsl.problem_class} <span style={{ color: '#888' }}>v{dsl.version}</span>
              </div>
              <div style={{ fontSize: '12px', color: '#ccc', marginTop: '4px' }}>
                {dsl.description}
              </div>
            </div>
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
              <div style={{ fontSize: '10px', color: '#666' }}>
                ID: {dsl.id}
              </div>
              <button
                onClick={() => onDelete(dsl.problem_class)}
                disabled={deleteLoading}
                style={{
                  background: 'rgba(255, 68, 68, 0.2)',
                  border: '1px solid #ff4444',
                  borderRadius: '6px',
                  padding: '6px 10px',
                  cursor: deleteLoading ? 'not-allowed' : 'pointer',
                  color: '#ff4444',
                  fontSize: '14px',
                  opacity: deleteLoading ? 0.5 : 1,
                  transition: 'all 0.2s ease',
                }}
                onMouseEnter={(e) => { if (!deleteLoading) e.currentTarget.style.background = 'rgba(255, 68, 68, 0.4)'; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = 'rgba(255, 68, 68, 0.2)'; }}
              >
                🗑️
              </button>
            </div>
          </div>
          {dsl.extensions.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
              {dsl.extensions.map(ext => (
                <span key={ext} style={{
                  background: '#a78bfa22', border: '1px solid #a78bfa44',
                  borderRadius: '4px', padding: '3px 8px', color: '#a78bfa',
                  fontSize: '10px', fontWeight: 700,
                }}>
                  {ext}
                </span>
              ))}
            </div>
          )}
          
          {/* スキーマ表示ボタン */}
          <button
            onClick={() => setExpandedId(expandedId === dsl.id ? null : dsl.id)}
            style={{
              background: expandedId === dsl.id ? '#00e5ff22' : 'transparent',
              border: `1px solid ${expandedId === dsl.id ? '#00e5ff' : '#444'}`,
              borderRadius: '6px',
              padding: '8px 12px',
              cursor: 'pointer',
              color: expandedId === dsl.id ? '#00e5ff' : '#888',
              fontSize: '12px',
              fontWeight: 700,
              marginTop: '8px',
              transition: 'all 0.2s ease',
            }}
          >
            {expandedId === dsl.id ? `📄 ${t('registryView.hideSchema')}` : `📄 ${t('registryView.showSchema')}`}
          </button>

          {/* スキーマJSON表示エリア */}
          {expandedId === dsl.id && dsl.schema_json && (
            <div style={{
              background: '#0a0a0c',
              border: '1px solid #333',
              borderRadius: '6px',
              padding: '12px',
              marginTop: '8px',
              maxHeight: '400px',
              overflow: 'auto',
            }}>
              <pre style={{
                margin: 0,
                color: '#a0d9a0',
                fontSize: '11px',
                fontFamily: 'monospace',
                lineHeight: '1.5',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}>
                {JSON.stringify(dsl.schema_json, null, 2)}
              </pre>
            </div>
          )}

          <div style={{ fontSize: '10px', color: '#666', marginTop: '4px' }}>
            {t('registryView.createdAt')}: {new Date(dsl.created_at).toLocaleString(i18n.language === 'en' ? 'en-US' : 'ja-JP')}
          </div>
        </div>
      ))}
    </div>
  );
}

// Extensionsタブ
function ExtensionsTab({ extensions }: { extensions: Extension[] }) {
  const { t } = useTranslation();
  const categories = extensions.reduce((acc, ext) => {
    const cat = ext.category || 'other';
    if (!acc[cat]) acc[cat] = [];
    acc[cat].push(ext);
    return acc;
  }, {} as Record<string, Extension[]>);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
      {Object.keys(categories).length === 0 && (
        <div style={{ color: '#888', textAlign: 'center', padding: '40px' }}>
          {t('registryView.noExtensions')}
        </div>
      )}
      {Object.entries(categories).map(([category, exts]) => (
        <div key={category}>
          <h3 style={{
            margin: '0 0 12px 0', color: '#a78bfa', fontSize: '14px',
            fontWeight: 900, textTransform: 'uppercase', letterSpacing: '1px',
          }}>
            📦 {category}
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {exts.map(ext => (
              <div key={ext.id} style={{
                background: '#1e1e24', border: '1px solid #2a2a30', borderRadius: '8px',
                padding: '12px', display: 'flex', justifyContent: 'space-between',
              }}>
                <div>
                  <div style={{ fontSize: '14px', fontWeight: 900, color: '#fff' }}>
                    {ext.name}
                  </div>
                  <div style={{ fontSize: '11px', color: '#ccc', marginTop: '4px' }}>
                    {ext.description}
                  </div>
                </div>
                <div style={{ fontSize: '10px', color: '#666' }}>
                  ID: {ext.id}
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// Solversタブ
function SolversTab({ solvers }: { solvers: Array<{ name: string; description: string; is_default: boolean }> }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
      {solvers.map(solver => (
        <div key={solver.name} style={{
          background: '#1e1e24', border: `1px solid ${solver.is_default ? '#1D9E75' : '#2a2a30'}`,
          borderRadius: '8px', padding: '16px',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontSize: '16px', fontWeight: 900, color: '#1D9E75' }}>
                {solver.name}
                {solver.is_default && (
                  <span style={{
                    marginLeft: '8px', fontSize: '10px', color: '#1D9E75',
                    background: '#1D9E7522', border: '1px solid #1D9E7544',
                    borderRadius: '4px', padding: '2px 6px',
                  }}>
                    DEFAULT
                  </span>
                )}
              </div>
              <div style={{ fontSize: '12px', color: '#ccc', marginTop: '4px' }}>
                {solver.description}
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// 進化ログタブ
function LogsTab({ logs, searchTerm }: { logs: EvolutionLog[]; searchTerm: string }) {
  const { t, i18n } = useTranslation();
  const filteredLogs = searchTerm
    ? logs.filter(log =>
        log.dsl_name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
        log.change_type?.toLowerCase().includes(searchTerm.toLowerCase()) ||
        log.business_context?.toLowerCase().includes(searchTerm.toLowerCase())
      )
    : logs;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
      {filteredLogs.length === 0 && (
        <div style={{ color: '#888', textAlign: 'center', padding: '40px' }}>
          {t('registryView.noLogs')}
        </div>
      )}
      {filteredLogs.map(log => (
        <div key={log.id} style={{
          background: '#1e1e24', border: '1px solid #2a2a30', borderRadius: '8px',
          padding: '16px', display: 'flex', flexDirection: 'column', gap: '8px',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <div style={{ fontSize: '14px', fontWeight: 900, color: '#ff9800' }}>
              {log.dsl_name}
            </div>
            <div style={{ fontSize: '10px', color: '#666' }}>
              {new Date(log.created_at).toLocaleString(i18n.language === 'en' ? 'en-US' : 'ja-JP')}
            </div>
          </div>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            <span style={{
              background: '#ff980022', border: '1px solid #ff980044',
              borderRadius: '4px', padding: '3px 8px', color: '#ff9800',
              fontSize: '10px', fontWeight: 700,
            }}>
              {log.change_type}
            </span>
            <span style={{
              background: '#00e5ff22', border: '1px solid #00e5ff44',
              borderRadius: '4px', padding: '3px 8px', color: '#00e5ff',
              fontSize: '10px', fontWeight: 700,
            }}>
              {log.trigger_type}
            </span>
            {log.extension_name && (
              <span style={{
                background: '#a78bfa22', border: '1px solid #a78bfa44',
                borderRadius: '4px', padding: '3px 8px', color: '#a78bfa',
                fontSize: '10px', fontWeight: 700,
              }}>
                {log.extension_name}
              </span>
            )}
          </div>
          {log.business_context && (
            <div style={{ fontSize: '11px', color: '#ccc' }}>
              {log.business_context}
            </div>
          )}
          {log.before_summary && (
            <div style={{ fontSize: '11px', color: '#888' }}>
              {t('registryView.before')}: {log.before_summary}
            </div>
          )}
          {log.after_summary && (
            <div style={{ fontSize: '11px', color: '#888' }}>
              {t('registryView.after')}: {log.after_summary}
            </div>
          )}
          <div style={{ fontSize: '10px', color: '#666' }}>
            {t('registryView.createdBy')}: {log.created_by}
          </div>
        </div>
      ))}
    </div>
  );
}
