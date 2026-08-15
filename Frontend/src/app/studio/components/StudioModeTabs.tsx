import { useTranslation } from 'react-i18next';
import type { StudioMode } from '../types.ts';

const YARD_MODES: { id: StudioMode; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'spatial',  label: 'Yard / Vessel' },
  { id: 'issues',   label: 'Issues' },
];

const DEMO_MODE_ID = 'infeasible' as const;

// 未知ドメイン（Generic4DSLView）専用タブ
// YardPlanning固有のタブ（Timeline / Yard・Vessel等）を出さないための汎用フォールバック
const GENERIC_MODES: { id: StudioMode; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'details',  label: 'Details'  },
  { id: 'issues',   label: 'Issues'   },
];

// YardPlanning系のタブを出してよい既知problem_class
const YARD_PROBLEM_CLASSES = ['YardPlanning', 'RCPSP'];

export function StudioModeTabs({
  mode, onModeChange, issueCount, problemClass,
}: {
  mode: StudioMode;
  onModeChange: (m: StudioMode) => void;
  issueCount: number;
  problemClass?: string;
}) {
  const { t } = useTranslation();
  // 2026-07-11: 基底パターンをTruckDispatcher/NurseShiftの2枚看板に整理する方針により、
  // EventStaffing/GhostKitchen/ProjectPlanner/BinPacking/CapacitatedVehicleRoutingProblem/
  // ProductionLotScheduler専用タブは削除。TruckDispatcher/NurseShiftはどちらも専用タブを
  // 持たず、GENERIC_MODES（Generic4DSLView）に一本化する。
  const MODES =
    problemClass && YARD_PROBLEM_CLASSES.includes(problemClass) ? YARD_MODES :
    problemClass ? GENERIC_MODES :
    YARD_MODES;

  const DEMO_MODES: { id: StudioMode; label: string }[] = [
    { id: DEMO_MODE_ID, label: t('studioModeTabs.constraintReview') },
  ];

  const ALL_MODES = [...MODES, ...DEMO_MODES];

  return (
    <div style={{
      flexShrink: 0, display: 'flex', gap: '6px', padding: '4px',
      background: '#121216', borderRadius: '10px', border: '1px solid #2a2a30',
    }}>
      {ALL_MODES.map(({ id, label }) => {
        const active = mode === id;
        const badge = id === 'issues' && issueCount > 0 ? issueCount : null;
        return (
          <button
            key={id}
            onClick={() => onModeChange(id)}
            style={{
              flex: 1, padding: '8px 12px',
              background: active ? '#00e5ff' : 'transparent',
              color: active ? '#000' : '#888',
              border: 'none', borderRadius: '6px', fontSize: '11px',
              fontWeight: 800, cursor: 'pointer', letterSpacing: '0.5px',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px',
            }}
          >
            {label}
            {badge !== null && (
              <span style={{
                fontSize: '9px', padding: '1px 6px', borderRadius: '8px',
                background: active ? '#005060' : '#ff4d4d',
                color: active ? '#00e5ff' : '#fff', fontWeight: 900,
              }}>
                {badge}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
