import type { Dsl } from '../domain/types.ts';
import React, { useState, useEffect, useLayoutEffect, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { RegisterModal } from './studio/components/RegisterModal.tsx';
import { RegistryView } from './RegistryView.tsx';
import LanguageToggle from '../i18n/LanguageToggle';

interface ScenarioCard {
  id: string | number;
  name: string;
  description: string;
  tag: string;
  tag_color: string;
  domain?: string;
  dsl_json: Dsl;
}

interface HomeScreenProps {
  onSelect: (dsl: Dsl, label?: string | null) => void;
  apiBase?: string;
}

// TAG_MAP: 既存ドメインのタグ表示設定
// 新規ドメインは自動登録パイプラインが _patch_home_screen_tag_map() で自動追加する
// 未登録の problem_class は getFallbackTag() でフォールバック処理される
const TAG_MAP: Record<string, { tag: string; tagColor: string; domain: string }> = {
  RCPSP:          { tag: 'YARD',     tagColor: '#00e5ff', domain: 'yard'            },
  YardPlanning:   { tag: 'YARD',     tagColor: '#00e5ff', domain: 'yard'            },
  TestDomain: { tag: 'TESTDO', tagColor: '#8b5cf6', domain: 'test_domain' },
  TestDomainV2: { tag: 'TESTDO', tagColor: '#8b5cf6', domain: 'test_domain_v2' },
  // 2026-07-11: EventStaffing/GhostKitchen/ProjectPlanner/BinPacking/
  // CapacitatedVehicleRoutingProblem/ProductionLotScheduler/HospitalShiftPlanner/StoreSite
  // は削除済み。TruckDispatcher/NurseShiftはgetFallbackTag()のフォールバック表示に任せる。
  LineChangeoverScheduler: { tag: 'LINECH', tagColor: '#8b5cf6', domain: 'line_changeover_scheduler' },
  MeetingRoom2: { tag: 'MEETIN', tagColor: '#8b5cf6', domain: 'meeting_room2' },
  MeetingRoom: { tag: 'MEETIN', tagColor: '#8b5cf6', domain: 'meeting_room' },
  StoreSite: { tag: 'STORES', tagColor: '#8b5cf6', domain: 'store_site' },
  CarSequencing: { tag: 'CARSEQ', tagColor: '#8b5cf6', domain: 'car_sequencing' },
  NursingWorkloadBalance: { tag: 'NURSIN', tagColor: '#8b5cf6', domain: 'nursing_workload_balance' },
  CapitalProjectSelector: { tag: 'CAPITA', tagColor: '#8b5cf6', domain: 'capital_project_selector' },
  CrewDutyScheduler: { tag: 'CREWDU', tagColor: '#8b5cf6', domain: 'crew_duty_scheduler' },
  ExampleDelivery: { tag: 'EXAMPL', tagColor: '#8b5cf6', domain: 'example_delivery' },
  InventoryReplenishmentPlanner: { tag: 'INVENT', tagColor: '#8b5cf6', domain: 'inventory_replenishment_planner' },
  AuctionWinnerSelector: { tag: 'AUCTIO', tagColor: '#8b5cf6', domain: 'auction_winner_selector' },
  PortfolioOverlapDesigner: { tag: 'PORTFO', tagColor: '#8b5cf6', domain: 'portfolio_overlap_designer' },
  TransportCostMinimizer: { tag: 'TRANSP', tagColor: '#8b5cf6', domain: 'transport_cost_minimizer' },
  VesselDeckLoader: { tag: 'VESSEL', tagColor: '#8b5cf6', domain: 'vessel_deck_loader' },
  DepotRoutePlanner: { tag: 'DEPOTR', tagColor: '#8b5cf6', domain: 'depot_route_planner' },
  SteelMillSlabDesign: { tag: 'STEELM', tagColor: '#8b5cf6', domain: 'steel_mill_slab_design' },
  ShiftRotationScheduler: { tag: 'SHIFTR', tagColor: '#8b5cf6', domain: 'shift_rotation_scheduler' },
  LotSizingScheduler: { tag: 'LOTSIZ', tagColor: '#8b5cf6', domain: 'lot_sizing_scheduler' },
  PatientTransportPlanner: { tag: 'PATIEN', tagColor: '#8b5cf6', domain: 'patient_transport_planner' },
  MedicalAppointmentScheduler: { tag: 'MEDICA', tagColor: '#8b5cf6', domain: 'medical_appointment_scheduler' },
  MedicalAppointmentSequenceScheduler: { tag: 'MEDICA', tagColor: '#8b5cf6', domain: 'medical_appointment_sequence_scheduler' },
  MysteryShopperScheduler: { tag: 'MYSTER', tagColor: '#8b5cf6', domain: 'mystery_shopper_scheduler' },
  EnergyCostAwareScheduler: { tag: 'ENERGY', tagColor: '#8b5cf6', domain: 'energy_cost_aware_scheduler' },
  TankAllocationPlanner: { tag: 'TANKAL', tagColor: '#8b5cf6', domain: 'tank_allocation_planner' },
  ProductionLineSequencing: { tag: 'PRODUC', tagColor: '#8b5cf6', domain: 'production_line_sequencing' },
  RideshareMatchingPlanner: { tag: 'RIDESH', tagColor: '#8b5cf6', domain: 'rideshare_matching_planner' },
  // ↓ 自動登録ドメインはここに自動追加される（domain_generator.py が管理）
};

// TAG_MAP 未登録の problem_class に対するフォールバック
// ハッシュで色を自動生成し、アップロードをエラーにしない
function getFallbackTag(problemClass: string): { tag: string; tagColor: string; domain: string } {
  let hash = 0;
  for (let i = 0; i < problemClass.length; i++) {
    hash = problemClass.charCodeAt(i) + ((hash << 5) - hash);
  }
  const hue = Math.abs(hash) % 360;
  const tagColor = `hsl(${hue}, 70%, 60%)`;
  const tag = problemClass.slice(0, 6).toUpperCase();
  const domain = problemClass.replace(/([A-Z])/g, '_$1').toLowerCase().replace(/^_/, '');
  return { tag, tagColor, domain };
}

export function HomeScreen({ onSelect, apiBase = "http://localhost:5000" }: HomeScreenProps) {
  const { t, i18n } = useTranslation();
  const [scenarios, setScenarios] = useState<ScenarioCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [searchText, setSearchText] = useState('');
  const fileInputRef = React.useRef<HTMLInputElement>(null);
  const refreshFileInputRef = React.useRef<HTMLInputElement>(null);
  const adhocFileInputRef = React.useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setLoading(true);
        // 2026-07-30 i18n対応: NurseShiftWeeklyCapの登録済みシナリオname/
        // descriptionをbackend側で現在言語に解決してもらうため?lang=を付与
        // （Backend/i18n/nurse_shift_weekly_cap_messages.py参照）。
        const res = await fetch(`${apiBase}/dsl_repository/scenarios?lang=${i18n.language}`);
        const data = await res.json();
        if (cancelled) return;
        if (data.status === 'ok') {
          setScenarios(data.scenarios);
        } else {
          setError(data.message || t('home.fetchScenariosFailed'));
        }
      } catch (e) {
        if (!cancelled) setError(`${t('home.connectionError')}: ${e}`);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [apiBase, refreshKey, i18n.language]);

  const handleSelect = (dsl: Dsl, label?: string | null) => {
    try { localStorage.removeItem("optibuddy_session"); } catch { /* ignore */ }
    onSelect(dsl, label);
  };

  const [showRegisterModal, setShowRegisterModal] = useState(false);
  const [showRegistryView, setShowRegistryView] = useState(false);

  const handleNewDomain   = () => setShowRegisterModal(true);
  const handleAddScenario = () => fileInputRef.current?.click();
  const handleShowRegistry = () => setShowRegistryView(true);

  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = async (e) => {
      try {
        const dsl  = JSON.parse(e.target?.result as string);
        const pc   = dsl.problem_class as string | undefined;
        const meta = pc ? (TAG_MAP[pc] ?? getFallbackTag(pc)) : { tag: 'CUSTOM', tagColor: '#888888', domain: 'custom' };

        const rawName = file.name.replace('.json', '');
        const name = (dsl.name as string | undefined)
          ?? rawName.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase());

        const res = await fetch(`${apiBase}/dsl_repository/scenarios`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name,
            description: (dsl.description as string | undefined) ?? t('home.userUploadDescription'),
            tag:       meta.tag,
            tag_color: meta.tagColor,
            domain:    meta.domain,
            dsl_json:  dsl,
          }),
        });

        const result = await res.json();
        if (result.status === 'ok') {
          setRefreshKey(k => k + 1);
        } else {
          alert(`${t('home.registerFailed')}: ${result.message}`);
        }
      } catch (err) {
        alert(t('home.dslParseFailed'));
        console.error(err);
      }
    };
    reader.readAsText(file);
    event.target.value = '';
  };

  const handleDelete = async (id: string | number, name: string) => {
    if (!confirm(t('home.confirmDelete', { name }))) return;
    try {
      const res = await fetch(`${apiBase}/dsl_repository/scenarios/${id}`, { method: 'DELETE' });
      if (res.ok) { setRefreshKey(k => k + 1); }
      else { alert(t('home.deleteFailed')); }
    } catch (err) { alert(t('home.deleteError')); console.error(err); }
  };

  const handleExport = async (id: string | number, name: string) => {
    try {
      const res = await fetch(`${apiBase}/dsl_repository/scenarios/${id}/export`);
      if (res.ok) {
        const blob = await res.blob();
        const url  = window.URL.createObjectURL(blob);
        const a    = document.createElement('a');
        a.href = url;
        a.download = `${name.replace(/\s+/g, '_')}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
      } else { alert(t('home.exportFailed')); }
    } catch (err) { alert(t('home.exportError')); console.error(err); }
  };

  // 登録済みシナリオのdsl_jsonを、ユーザーがローカルで選んだJSONファイルの内容で
  // 上書きする（サーバー側でファイルパスを探索・保存することはしない。元のDSL JSONが
  // どれか分かっていればそれを直接編集し、分からなければ💾エクスポートでダンプした
  // ものを編集して、その結果のファイルをここで選んでもらう運用）。
  const [refreshingId, setRefreshingId] = useState<string | number | null>(null);
  const [refreshTarget, setRefreshTarget] = useState<{ id: string | number; name: string } | null>(null);

  const handleRefreshClick = (id: string | number, name: string) => {
    setRefreshTarget({ id, name });
    refreshFileInputRef.current?.click();
  };

  const handleRefreshFileSelected = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    const target = refreshTarget;
    event.target.value = '';
    if (!file || !target) return;

    const reader = new FileReader();
    reader.onload = async (e) => {
      let dsl_json: unknown;
      try {
        dsl_json = JSON.parse(e.target?.result as string);
      } catch (err) {
        alert(t('home.refreshParseFailed'));
        console.error(err);
        return;
      }
      setRefreshingId(target.id);
      try {
        const res = await fetch(`${apiBase}/dsl_repository/scenarios/${target.id}/refresh`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ dsl_json }),
        });
        const data = await res.json();
        if (data.status !== 'ok') { alert(`${t('home.refreshError')}: ${data.message ?? ''}`); return; }
        const result = data.result ?? {};
        switch (result.status) {
          case 'updated':
            alert(t('home.refreshUpdated', { name: target.name }));
            setRefreshKey(k => k + 1);
            break;
          case 'no_change':
            alert(t('home.refreshNoChange', { name: target.name }));
            break;
          case 'not_found':
            alert(t('home.refreshNotFound'));
            break;
          default:
            alert(`${t('home.refreshError')}: ${result.status ?? 'unknown'}`);
        }
      } catch (err) {
        alert(t('home.refreshError'));
        console.error(err);
      } finally {
        setRefreshingId(null);
        setRefreshTarget(null);
      }
    };
    reader.readAsText(file);
  };

  // 外部JSONを「入力DSLとして」選択したドメインに投入し、DB登録を挟まずに
  // 即座にStudio画面へ遷移して結果を表示する（社内テスト用途、アドホック実行）。
  // 設計: docs/DESIGN_2026-08-18_home_domain_filter_and_json_dsl_import.md
  const [adhocTarget, setAdhocTarget] = useState<{ problemClass?: string; name: string } | null>(null);

  const handleAdhocRunClick = (scenario: ScenarioCard) => {
    setAdhocTarget({ problemClass: scenario.dsl_json?.problem_class, name: scenario.name });
    adhocFileInputRef.current?.click();
  };

  const handleAdhocFileSelected = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    const target = adhocTarget;
    event.target.value = '';
    if (!file || !target) return;

    const reader = new FileReader();
    reader.onload = (e) => {
      let dsl: Record<string, unknown>;
      try {
        dsl = JSON.parse(e.target?.result as string);
      } catch (err) {
        alert(t('home.adhocParseFailed'));
        console.error(err);
        return;
      }
      if (typeof dsl !== 'object' || dsl === null || Array.isArray(dsl)) {
        alert(t('home.adhocInvalidTopLevel'));
        return;
      }

      // problem_class不一致時の扱い（確定方針: 自動補完＋非ブロッキング通知）
      const meta = dsl.metadata as { problem_class?: string } | undefined;
      const uploadedPc = (dsl.problem_class as string | undefined) ?? meta?.problem_class;
      let mismatchNote = '';
      if (!uploadedPc) {
        if (target.problemClass) dsl.problem_class = target.problemClass;
      } else if (target.problemClass && uploadedPc !== target.problemClass) {
        mismatchNote = ` ${t('home.adhocMismatchNote', { pc: uploadedPc })}`;
      }

      const rawName = file.name.replace(/\.json$/i, '');
      const displayName = (dsl.name as string | undefined) ?? rawName;
      const label = `${displayName} ${t('home.adhocLabelSuffix')}${mismatchNote}`;
      handleSelect(dsl as unknown as Dsl, label);
    };
    reader.readAsText(file);
  };

  // ドメインフィルター機能: シナリオ名（name）、表示名（tag）、内部ドメインキー（domain, snake_case）の
  // 部分一致（大文字小文字無視）で絞り込む。
  const filteredScenarios = useMemo(() => {
    const q = searchText.trim().toLowerCase();
    if (!q) return scenarios;
    return scenarios.filter((s) =>
      (s.name ?? '').toLowerCase().includes(q) ||
      (s.tag ?? '').toLowerCase().includes(q) ||
      (s.domain ?? '').toLowerCase().includes(q)
    );
  }, [scenarios, searchText]);

  return (
    <div style={{ width: "100vw", minHeight: "100vh", background: "#08080a", color: "#eee", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "flex-start", padding: "40px 20px", boxSizing: "border-box", fontFamily: "monospace", gap: "32px" }}>
      <div style={{ position: "fixed", top: 16, right: 16 }}>
        <LanguageToggle />
      </div>
      <div style={{ textAlign: "center" }}>
        <div style={{ fontSize: "10px", color: "#555", letterSpacing: "4px", marginBottom: "12px" }}>OptiBuddy</div>
        <h1 style={{ fontSize: "2rem", fontWeight: 900, margin: 0, color: "#fff", letterSpacing: "-1px", lineHeight: 1 }}>{t('home.selectScenario')}</h1>
        <p style={{ marginTop: "12px", color: "#555", fontSize: "13px" }}>{t('home.subtitle')}</p>
      </div>

      <div style={{ display: "flex", gap: "12px" }}>
        <button onClick={handleNewDomain}    style={{ border: "1px solid #1D9E7544", borderRadius: "8px", background: "transparent", color: "#1D9E75", padding: "10px 18px", fontSize: "13px", fontWeight: 800, cursor: "pointer", letterSpacing: "0.5px" }}>✨ {t('home.registerNewDomain')}</button>
        <button onClick={handleAddScenario}  style={{ border: "1px solid #00e5ff44", borderRadius: "8px", background: "transparent", color: "#00e5ff", padding: "10px 18px", fontSize: "13px", fontWeight: 800, cursor: "pointer", letterSpacing: "0.5px" }}>📁 {t('home.addScenario')}</button>
        <button onClick={handleShowRegistry} style={{ border: "1px solid #a78bfa44", borderRadius: "8px", background: "transparent", color: "#a78bfa", padding: "10px 18px", fontSize: "13px", fontWeight: 800, cursor: "pointer", letterSpacing: "0.5px" }}>📚 {t('home.showRegistry')}</button>
      </div>

      <input ref={fileInputRef} type="file" accept=".json" style={{ display: 'none' }} onChange={handleFileUpload} />
      <input ref={refreshFileInputRef} type="file" accept=".json" style={{ display: 'none' }} onChange={handleRefreshFileSelected} />
      <input ref={adhocFileInputRef} type="file" accept=".json" style={{ display: 'none' }} onChange={handleAdhocFileSelected} />

      {!loading && !error && scenarios.length > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: "8px", width: "100%", maxWidth: "1200px" }}>
          <span style={{ fontSize: "12px", color: "#555" }}>🔍</span>
          <input
            type="text"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            placeholder={t('home.filterPlaceholder')}
            style={{ flex: 1, background: "#121216", border: `1px solid ${searchText ? "#00e5ff" : "#2a2a30"}`, color: "#eee", borderRadius: "6px", fontSize: "12px", padding: "8px 12px", outline: "none", fontFamily: "monospace" }}
          />
          {searchText && (
            <button
              onClick={() => setSearchText('')}
              style={{ background: "transparent", border: "none", color: "#555", cursor: "pointer", fontSize: "16px", padding: "0 4px" }}
            >×</button>
          )}
        </div>
      )}

      {loading && <div style={{ color: "#888" }}>{t('home.loading')}</div>}
      {error   && <div style={{ color: "#ff4444" }}>⚠️ {error}</div>}

      {!loading && !error && filteredScenarios.length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: "16px", width: "100%", maxWidth: "1200px" }}>
          {filteredScenarios.map((scenario) => (
            <PresetCard key={scenario.id} preset={scenario} onSelect={handleSelect} onDelete={handleDelete} onExport={handleExport}
              onRefresh={handleRefreshClick} onAdhocRun={handleAdhocRunClick} refreshing={refreshingId === scenario.id} />
          ))}
        </div>
      )}

      {!loading && !error && scenarios.length === 0 && (
        <div style={{ color: "#888", fontSize: "14px" }}>{t('home.noScenarios')}</div>
      )}

      {!loading && !error && scenarios.length > 0 && filteredScenarios.length === 0 && (
        <div style={{ color: "#888", fontSize: "14px" }}>{t('home.noFilterMatches')}</div>
      )}

      {showRegisterModal && <RegisterModal onClose={() => setShowRegisterModal(false)} />}
      {showRegistryView  && <RegistryView  onClose={() => setShowRegistryView(false)} apiBase={apiBase} />}
    </div>
  );
}

function PresetCard({ preset, onSelect, onDelete, onExport, onRefresh, onAdhocRun, refreshing }: {
  preset: ScenarioCard;
  onSelect: (dsl: Dsl, label?: string | null) => void;
  onDelete: (id: string | number, name: string) => void;
  onExport: (id: string | number, name: string) => void;
  onRefresh: (id: string | number, name: string) => void;
  onAdhocRun: (scenario: ScenarioCard) => void;
  refreshing: boolean;
}) {
  const { t } = useTranslation();
  const [hovered, setHovered] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [needsToggle, setNeedsToggle] = useState(false);
  const descRef = useRef<HTMLDivElement>(null);

  // 折りたたみ(2行clamp)時点で実際にあふれているかを測定し、
  // あふれていない短い説明文には開閉トグル自体を出さない
  useLayoutEffect(() => {
    const el = descRef.current;
    if (!el) return;
    setNeedsToggle(el.scrollHeight > el.clientHeight + 1);
  }, [preset.description]);

  const containerCount = preset.dsl_json?.containers?.length || 0;
  const craneCount = (preset.dsl_json?.resources?.yard_cranes?.length || 0) + (preset.dsl_json?.resources?.ship_cranes?.length || 0);

  return (
    <div
      style={{ background: hovered ? "#16161c" : "#121216", border: `1px solid ${hovered ? preset.tag_color + "55" : "#2a2a30"}`, borderRadius: "12px", padding: "24px", cursor: "pointer", transition: "all 0.15s ease", display: "flex", flexDirection: "column", gap: "12px", transform: hovered ? "translateY(-2px)" : "none", boxShadow: hovered ? "0 8px 24px rgba(0,0,0,0.4)" : "none", position: "relative" }}
      onClick={() => onSelect(preset.dsl_json, preset.name)}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {hovered && (
        <button onClick={(e) => { e.stopPropagation(); onAdhocRun(preset); }}
          title={t('home.adhocRunTooltip')}
          style={{ position: "absolute", top: "8px", right: "128px", background: "rgba(29,158,117,0.2)", border: "1px solid #1D9E75", borderRadius: "4px", padding: "4px 8px", cursor: "pointer", fontSize: "12px", color: "#1D9E75" }}
          onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(29,158,117,0.3)"; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = "rgba(29,158,117,0.2)"; }}
        >🚀</button>
      )}
      {hovered && (
        <button onClick={(e) => { e.stopPropagation(); if (!refreshing) onRefresh(preset.id, preset.name); }}
          title={t('home.refreshTooltip')}
          style={{ position: "absolute", top: "8px", right: "88px", background: "rgba(167,139,250,0.2)", border: "1px solid #a78bfa", borderRadius: "4px", padding: "4px 8px", cursor: refreshing ? "wait" : "pointer", fontSize: "12px", color: "#a78bfa", opacity: refreshing ? 0.5 : 1 }}
          onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(167,139,250,0.3)"; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = "rgba(167,139,250,0.2)"; }}
        >{refreshing ? "…" : "🔄"}</button>
      )}
      {hovered && (
        <button onClick={(e) => { e.stopPropagation(); onExport(preset.id, preset.name); }}
          title={t('home.exportTooltip')}
          style={{ position: "absolute", top: "8px", right: "48px", background: "rgba(0,229,255,0.2)", border: "1px solid #00e5ff", borderRadius: "4px", padding: "4px 8px", cursor: "pointer", fontSize: "12px", color: "#00e5ff" }}
          onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(0,229,255,0.3)"; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = "rgba(0,229,255,0.2)"; }}
        >💾</button>
      )}
      {hovered && (
        <button onClick={(e) => { e.stopPropagation(); onDelete(preset.id, preset.name); }}
          title={t('home.deleteTooltip')}
          style={{ position: "absolute", top: "8px", right: "8px", background: "rgba(255,68,68,0.2)", border: "1px solid #ff4444", borderRadius: "4px", padding: "4px 8px", cursor: "pointer", fontSize: "12px", color: "#ff4444" }}
          onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(255,68,68,0.3)"; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = "rgba(255,68,68,0.2)"; }}
        >🗑️</button>
      )}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <span style={{ display: "inline-block", fontSize: "9px", fontWeight: 900, letterSpacing: "2px", color: preset.tag_color, border: `1px solid ${preset.tag_color}44`, borderRadius: "4px", padding: "3px 8px", background: `${preset.tag_color}11` }}>{preset.tag}</span>
        <span style={{ fontSize: "10px", color: "#333", fontWeight: 900 }}>{containerCount}C / {craneCount}CR</span>
      </div>
      <div>
        <div style={{ fontSize: "16px", fontWeight: 900, color: "#fff", marginBottom: "8px" }}>{preset.name}</div>
        <div
          ref={descRef}
          style={{
            fontSize: "12px",
            color: "#ccc",
            lineHeight: 1.6,
            display: expanded ? "block" : "-webkit-box",
            WebkitLineClamp: expanded ? "unset" : 2,
            WebkitBoxOrient: "vertical",
            overflow: expanded ? "visible" : "hidden",
          } as React.CSSProperties}
        >
          {preset.description}
        </div>
        {needsToggle && (
          <button
            onClick={(e) => { e.stopPropagation(); setExpanded((v) => !v); }}
            style={{ background: "none", border: "none", padding: "10px 0 2px", margin: 0, fontSize: "11px", color: "#666", cursor: "pointer", display: "flex", alignItems: "center", gap: "4px", fontFamily: "inherit", width: "100%", textAlign: "left" }}
          >
            <span style={{ display: "inline-block", transition: "transform 0.15s ease", transform: expanded ? "rotate(90deg)" : "none" }}>▸</span>
            {expanded ? t('home.collapseDescription') : t('home.expandDescription')}
          </button>
        )}
      </div>
      <div style={{ marginTop: "auto", paddingTop: "12px", borderTop: "1px solid #1e1e24", display: "flex", alignItems: "center", gap: "6px", fontSize: "11px", color: hovered ? preset.tag_color : "#444", fontWeight: 700, transition: "color 0.15s ease" }}>
        <span>▶</span><span>SELECT & RUN SOLVER</span>
      </div>
    </div>
  );
}
