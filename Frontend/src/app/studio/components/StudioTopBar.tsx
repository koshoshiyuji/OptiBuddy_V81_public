
import { useTranslation } from 'react-i18next';
import type { StudioState } from '../types.ts';
import LanguageToggle from '../../../i18n/LanguageToggle';

type PickState = Pick<
  StudioState,
  | 'onBackToHome'
  | 'scenarioLabel'
  | 'searchQuery'
  | 'setSearchQuery'
  | 'searchHitIds'
  | 'tasks'
  | 'setHighlightId'
  | 'initialInputData'
>;

export function StudioTopBar({ studio }: { studio: PickState }) {
  const { t } = useTranslation();
  const { onBackToHome, searchQuery, setSearchQuery, searchHitIds, tasks, setHighlightId, initialInputData } = studio;

  const _pc =
    (initialInputData as { metadata?: { problem_class?: string }; problem_class?: string })
      .metadata?.problem_class ??
    (initialInputData as { problem_class?: string }).problem_class;
  // 2026-07-11: EventStaffingは削除済み。isStaffingは常にfalseになるため、以降の
  // 検索ロジック（isStaffing分岐）は無害な未到達コードとして残置（動作に影響なし）。
  const isStaffing = _pc === 'EventStaffing';
  const isYard = _pc === 'YardPlanning' || _pc === 'RCPSP';

  // YardPlanning/RCPSP以外は、problem_classから動的にラベルを生成する。
  // YardPlanning固有の "CONTAINER TERMINAL OPTIMIZER" 文言を他ドメインに出さないため。
  const optimizerSuffix = t('studioTopBar.optimizerSuffix');
  const topBarLabel = isYard
    ? t('studioTopBar.containerTerminalOptimizer')
    : _pc
      ? `${_pc.replace(/([a-z])([A-Z])/g, '$1 $2').toUpperCase()} ${optimizerSuffix}`
      : optimizerSuffix;


  const placeholder = isStaffing ? t('studioTopBar.searchPlaceholderStaffing') : t('studioTopBar.searchPlaceholderContainer');

  const handleSearch = (q: string) => {
    setSearchQuery(q);
    const lower = q.trim().toLowerCase();
    if (!lower) { setHighlightId({ kind: 'none' }); return; }

    let hits: string[];
    let matched: typeof tasks = [];
    
    if (isStaffing) {
      matched = tasks.filter((t) => {
        const staffName = t.resource ?? (t as { staff_name?: string }).staff_name ?? '';
        const taskName  = (t as { task_name?: string }).task_name ?? t.containerId ?? '';
        const taskId    = (t as { task_id?: string }).task_id ?? '';
        
        console.log("Checking task:", { 
          id: t.id, 
          staffName, 
          taskName, 
          taskId, 
          resource: t.resource,
          containerId: t.containerId,
          fullTask: t 
        });
        
        return staffName.toLowerCase().includes(lower) 
            || taskName.toLowerCase().includes(lower)
            || taskId.toLowerCase().includes(lower);
      });

      console.log("Matched tasks:", matched.length, matched);

      // タスクIDをハイライト対象として設定（ガントチャートでのhover判定用）
      hits = Array.from(new Set(
        matched.map((t) => t.id)
      )).filter((id): id is string => Boolean(id));
      
      // EventStaffingの場合、containerId も hits に追加（空でない場合のみ）
      const containerIds = matched
        .map((t) => t.containerId)
        .filter((cid): cid is string => Boolean(cid) && cid.trim() !== '');
      hits = Array.from(new Set([...hits, ...containerIds]));
      
    } else {
      const allIds = Array.from(new Set(tasks.map((t) => t.containerId)));
      hits = allIds.filter((id) => id.toLowerCase().includes(lower));
    }

    console.log("Search hits:", hits);

    if (hits.length > 0) {
      setHighlightId({
        kind: 'active',
        hoverContainerId: matched[0]?.containerId ?? hits[0],
        selectedIssueId: hits[0],
        relatedContainerIds: hits,
      });
    }
  };

  return (
    <div
      style={{
        flexShrink: 0,
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: '0 4px',
        gap: '10px',
      }}
    >
      {onBackToHome && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <button
            onClick={onBackToHome}
            style={{
              background: 'transparent',
              border: '1px solid #2a2a30',
              color: '#555',
              padding: '5px 12px',
              borderRadius: '6px',
              fontSize: '11px',
              fontWeight: 700,
              cursor: 'pointer',
              fontFamily: 'monospace',
              letterSpacing: '1px',
              flexShrink: 0,
            }}
            onMouseEnter={(e) => {
              (e.target as HTMLButtonElement).style.color = '#eee';
              (e.target as HTMLButtonElement).style.borderColor = '#555';
            }}
            onMouseLeave={(e) => {
              (e.target as HTMLButtonElement).style.color = '#555';
              (e.target as HTMLButtonElement).style.borderColor = '#2a2a30';
            }}
          >
            {t('studioTopBar.backToHome')}
          </button>
          <span style={{ fontSize: '12px', color: '#eee', fontWeight: 700, whiteSpace: 'nowrap' }}>
            【{studio.scenarioLabel?.trim() ? studio.scenarioLabel : t('studioTopBar.dataInputLabel')}】
          </span>
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flex: 1, maxWidth: '300px' }}>
        <span style={{ fontSize: '10px', color: '#555', fontWeight: 800, whiteSpace: 'nowrap' }}>🔍</span>
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => handleSearch(e.target.value)}
          placeholder={placeholder}
          style={{
            flex: 1,
            background: '#121216',
            border: `1px solid ${searchQuery ? '#00e5ff' : '#2a2a30'}`,
            color: '#eee',
            borderRadius: '6px',
            fontSize: '11px',
            padding: '4px 10px',
            outline: 'none',
            fontFamily: 'monospace',
          }}
        />
        {searchQuery && (
          <button
            onClick={() => { setSearchQuery(''); setHighlightId({ kind: 'none' }); }}
            style={{ background: 'transparent', border: 'none', color: '#555', cursor: 'pointer', fontSize: '14px', padding: '0 4px' }}
          >
            ×
          </button>
        )}
        {searchQuery && (
          <span style={{
            fontSize: '10px',
            color: searchHitIds.size > 0 ? '#00e5ff' : '#ff4444',
            whiteSpace: 'nowrap',
            fontWeight: 700,
          }}>
            {t('studioTopBar.hitsCount', { count: searchHitIds.size })}
          </span>
        )}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginLeft: 'auto', flexShrink: 0 }}>
        <div style={{ fontSize: '10px', color: '#333', letterSpacing: '2px' }}>
          {topBarLabel}
        </div>
        <LanguageToggle />
      </div>
    </div>
  );
}
