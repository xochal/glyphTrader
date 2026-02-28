import React, { useState, useEffect, useCallback } from 'react';
import api from '../services/api';

const s = {
  card: { background: '#1a1a2e', borderRadius: '8px', padding: '20px', border: '1px solid #2a2a4a', marginBottom: '16px' } as React.CSSProperties,
  sectionHeader: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer', userSelect: 'none' as const },
  sectionTitle: { fontSize: '15px', fontWeight: 'bold', color: '#a0a0c0' },
  chevron: { color: '#666', fontSize: '14px' },
  label: { display: 'block', fontSize: '12px', color: '#666', marginBottom: '4px', textTransform: 'uppercase' as const },
  input: { width: '100%', padding: '8px 12px', background: '#0f0f1a', border: '1px solid #2a2a4a', borderRadius: '6px', color: '#e0e0e0', fontSize: '14px', marginBottom: '12px', boxSizing: 'border-box' as const },
  inputModified: { borderColor: '#3388ff' },
  btn: { padding: '8px 20px', background: '#00d4aa', color: '#0f0f1a', border: 'none', borderRadius: '6px', fontSize: '13px', fontWeight: 'bold', cursor: 'pointer', marginRight: '8px' } as React.CSSProperties,
  btnOutline: { padding: '8px 20px', background: 'transparent', color: '#a0a0c0', border: '1px solid #444', borderRadius: '6px', fontSize: '13px', cursor: 'pointer', marginRight: '8px' } as React.CSSProperties,
  btnDanger: { padding: '8px 20px', background: '#ff4444', color: '#fff', border: 'none', borderRadius: '6px', fontSize: '13px', fontWeight: 'bold', cursor: 'pointer' } as React.CSSProperties,
  msg: { fontSize: '13px', padding: '8px', borderRadius: '4px', marginBottom: '12px' },
  dot: { display: 'inline-block', width: '8px', height: '8px', borderRadius: '50%', background: '#3388ff', marginLeft: '6px' },
  row: { display: 'flex', gap: '12px', flexWrap: 'wrap' as const },
  field: { flex: '1', minWidth: '140px' },
  badge: { fontSize: '10px', padding: '2px 6px', borderRadius: '4px', marginLeft: '6px', fontWeight: 'bold' } as React.CSSProperties,
  readOnly: { fontSize: '13px', color: '#888', padding: '8px 12px', background: '#0a0a15', borderRadius: '6px', marginBottom: '8px' },
  table: { width: '100%', borderCollapse: 'collapse' as const, fontSize: '12px' },
  th: { padding: '6px 8px', textAlign: 'left' as const, color: '#666', borderBottom: '1px solid #2a2a4a', textTransform: 'uppercase' as const, fontSize: '11px' },
  td: { padding: '6px 8px', borderBottom: '1px solid #1a1a2e' },
  tdInput: { padding: '4px', background: '#0f0f1a', border: '1px solid #2a2a4a', borderRadius: '4px', color: '#e0e0e0', fontSize: '12px', width: '100%', boxSizing: 'border-box' as const },
};

interface Overrides { trading_params: Record<string, any>; watchlist: Record<string, any> }

function Section({ title, children, hasOverrides, defaultOpen }: { title: string; children: React.ReactNode; hasOverrides?: boolean; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen || false);
  return (
    <div style={s.card}>
      <div style={s.sectionHeader} onClick={() => setOpen(!open)}>
        <span style={s.sectionTitle}>{title}{hasOverrides && <span style={s.dot} title="Has overrides" />}</span>
        <span style={s.chevron}>{open ? '\u25B2' : '\u25BC'}</span>
      </div>
      {open && <div style={{ marginTop: '16px' }}>{children}</div>}
    </div>
  );
}

function Field({ label, value, defaultVal, onChange, type = 'number', disabled = false }: {
  label: string; value: any; defaultVal?: any; onChange: (v: any) => void; type?: string; disabled?: boolean;
}) {
  const isModified = defaultVal !== undefined && value !== undefined && value !== '' && Number(value) !== Number(defaultVal);
  return (
    <div style={s.field}>
      <label style={s.label}>{label}{isModified && <span style={s.dot} />}</label>
      <input
        style={{ ...s.input, ...(isModified ? s.inputModified : {}) }}
        type={type}
        value={value ?? ''}
        placeholder={defaultVal !== undefined ? String(defaultVal) : ''}
        onChange={e => onChange(type === 'number' ? (e.target.value === '' ? '' : Number(e.target.value)) : e.target.value)}
        disabled={disabled}
        step={type === 'number' ? 'any' : undefined}
      />
    </div>
  );
}

export default function TradeSettings() {
  const [defaults, setDefaults] = useState<any>(null);
  const [merged, setMerged] = useState<any>(null);
  const [overrides, setOverrides] = useState<Overrides>({ trading_params: {}, watchlist: {} });
  const [msg, setMsg] = useState({ text: '', type: '' });
  const [editState, setEditState] = useState<Record<string, any>>({});
  const [watchlistEdits, setWatchlistEdits] = useState<Record<string, any>>({});
  const [newStock, setNewStock] = useState({ symbol: '', sector: 'Custom', tier: 3, v4_threshold: 75, benchmark_index: 'SPY', tier_size_multiplier: 1.0 });

  const loadConfig = useCallback(async () => {
    try {
      const [mRes, dRes, oRes] = await Promise.all([
        api.get('/trade-settings/'),
        api.get('/trade-settings/defaults'),
        api.get('/trade-settings/overrides'),
      ]);
      setMerged(mRes.data);
      setDefaults(dRes.data);
      setOverrides(oRes.data);
      setEditState({});
      setWatchlistEdits({});
    } catch {
      setMsg({ text: 'Failed to load config', type: 'error' });
    }
  }, []);

  useEffect(() => { loadConfig(); }, [loadConfig]);

  const showMsg = (text: string, type: string) => {
    setMsg({ text, type });
    setTimeout(() => setMsg({ text: '', type: '' }), 4000);
  };

  const getEdit = (section: string, field: string) => editState[section]?.[field];
  const getMerged = (section: string, field: string) => merged?.trading_params?.[section]?.[field];
  const getDefault = (section: string, field: string) => defaults?.trading_params?.[section]?.[field];
  const hasOverride = (section: string) => section in overrides.trading_params;

  const setEdit = (section: string, field: string, value: any) => {
    setEditState(prev => ({
      ...prev,
      [section]: { ...prev[section], [field]: value },
    }));
  };

  const val = (section: string, field: string) => {
    const edit = getEdit(section, field);
    if (edit !== undefined && edit !== '') return edit;
    return getMerged(section, field);
  };

  const saveSection = async (section: string) => {
    const sectionEdits = editState[section];
    if (!sectionEdits || Object.keys(sectionEdits).length === 0) {
      showMsg('No changes to save', 'error');
      return;
    }
    // Filter out empty strings
    const cleaned: Record<string, any> = {};
    for (const [k, v] of Object.entries(sectionEdits)) {
      if (v !== '' && v !== undefined) cleaned[k] = v;
    }
    if (Object.keys(cleaned).length === 0) return;

    try {
      await api.put('/trade-settings/params', { overrides: { [section]: cleaned } });
      showMsg(`${section} saved`, 'success');
      loadConfig();
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      const errorText = detail?.errors ? detail.errors.join(', ') : (typeof detail === 'string' ? detail : 'Save failed');
      showMsg(errorText, 'error');
    }
  };

  const saveTopLevel = async (key: string, value: any) => {
    try {
      await api.put('/trade-settings/params', { overrides: { [key]: value } });
      showMsg(`${key} saved`, 'success');
      loadConfig();
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      const errorText = detail?.errors ? detail.errors.join(', ') : (typeof detail === 'string' ? detail : 'Save failed');
      showMsg(errorText, 'error');
    }
  };

  const resetSection = async (section: string) => {
    if (!window.confirm(`Reset ${section} to defaults?`)) return;
    try {
      await api.post(`/trade-settings/reset/${section}`);
      showMsg(`${section} reset`, 'success');
      loadConfig();
    } catch {
      showMsg('Reset failed', 'error');
    }
  };

  const resetAll = async () => {
    if (!window.confirm('Reset ALL settings to defaults? This cannot be undone.')) return;
    try {
      await api.post('/trade-settings/reset');
      showMsg('All settings reset to defaults', 'success');
      loadConfig();
    } catch {
      showMsg('Reset failed', 'error');
    }
  };

  // Watchlist helpers
  const saveStock = async (stock: any) => {
    try {
      await api.put('/trade-settings/watchlist/stock', stock);
      showMsg(`${stock.symbol} saved`, 'success');
      loadConfig();
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      const errorText = detail?.errors ? detail.errors.join(', ') : 'Save failed';
      showMsg(errorText, 'error');
    }
  };

  const removeStock = async (symbol: string) => {
    if (!window.confirm(`Remove ${symbol} from watchlist?`)) return;
    try {
      await api.delete(`/trade-settings/watchlist/stock/${symbol}`);
      showMsg(`${symbol} removed`, 'success');
      loadConfig();
    } catch {
      showMsg('Remove failed', 'error');
    }
  };

  const addStock = async () => {
    if (!newStock.symbol) return;
    await saveStock(newStock);
    setNewStock({ symbol: '', sector: 'Custom', tier: 3, v4_threshold: 75, benchmark_index: 'SPY', tier_size_multiplier: 1.0 });
  };

  if (!merged || !defaults) return <div style={{ color: '#666' }}>Loading...</div>;

  const tp = merged.trading_params;
  const dp = defaults.trading_params;
  const stocks = merged.watchlist?.stocks || [];
  const defaultSymbols = new Set((defaults.watchlist?.stocks || []).map((s: any) => s.symbol));
  const removedSymbols = new Set(overrides.watchlist?.watchlist_remove || []);
  const modifiedSymbols = new Set(Object.keys(overrides.watchlist?.watchlist_modify || {}));
  const addedSymbols = new Set((overrides.watchlist?.watchlist_add || []).map((s: any) => s.symbol));

  const exitPctSum = (val('atr_exits', 't1_exit_pct') || 0) + (val('atr_exits', 't2_exit_pct') || 0) + (val('atr_exits', 't3_exit_pct') || 0);

  return (
    <div>
      <h1 style={{ fontSize: '20px', marginBottom: '24px' }}>Trade Settings</h1>

      {msg.text && (
        <div style={{ ...s.msg, background: msg.type === 'error' ? '#2a1a1a' : '#1a2a1a', color: msg.type === 'error' ? '#ff4444' : '#00d4aa', border: `1px solid ${msg.type === 'error' ? '#441a1a' : '#1a441a'}` }}>
          {msg.text}
        </div>
      )}

      {/* 1. Position Sizing */}
      <Section title="Position Sizing" hasOverrides={hasOverride('position_sizing')}>
        <div style={s.row}>
          <Field label="Initial Position %" value={val('position_sizing', 'initial_pct')} defaultVal={dp.position_sizing.initial_pct} onChange={v => setEdit('position_sizing', 'initial_pct', v)} />
          <Field label="Pyramid %" value={val('position_sizing', 'pyramid_pct')} defaultVal={dp.position_sizing.pyramid_pct} onChange={v => setEdit('position_sizing', 'pyramid_pct', v)} />
          <Field label="Max Per Stock %" value={val('position_sizing', 'max_per_stock_pct')} defaultVal={dp.position_sizing.max_per_stock_pct} onChange={v => setEdit('position_sizing', 'max_per_stock_pct', v)} />
        </div>
        <div style={{ marginBottom: '12px' }}>
          <label style={{ fontSize: '13px', color: '#a0a0c0', cursor: 'pointer' }}>
            <input type="checkbox" checked={tp.position_sizing.use_margin || false} onChange={e => { setEdit('position_sizing', 'use_margin', e.target.checked); }} style={{ marginRight: '8px' }} />
            Use Margin
          </label>
        </div>
        <div>
          <button style={s.btn} onClick={() => saveSection('position_sizing')}>Save</button>
          {hasOverride('position_sizing') && <button style={s.btnOutline} onClick={() => resetSection('position_sizing')}>Reset</button>}
        </div>
      </Section>

      {/* 2. Entry Filters */}
      <Section title="Entry Filters" hasOverrides={hasOverride('filters')}>
        <div style={s.row}>
          <Field label="EMA Fast" value={val('filters', 'ema_fast')} defaultVal={dp.filters.ema_fast} onChange={v => setEdit('filters', 'ema_fast', v)} />
          <Field label="EMA Slow" value={val('filters', 'ema_slow')} defaultVal={dp.filters.ema_slow} onChange={v => setEdit('filters', 'ema_slow', v)} />
          <Field label="Price Move ATR Mult" value={val('filters', 'price_movement_atr_mult')} defaultVal={dp.filters.price_movement_atr_mult} onChange={v => setEdit('filters', 'price_movement_atr_mult', v)} />
        </div>
        <div style={s.row}>
          <Field label="Max Slippage %" value={val('filters', 'max_slippage_pct')} defaultVal={dp.filters.max_slippage_pct} onChange={v => setEdit('filters', 'max_slippage_pct', v)} />
          <Field label="Regime VIX Max" value={val('filters', 'regime_vix_max')} defaultVal={dp.filters.regime_vix_max} onChange={v => setEdit('filters', 'regime_vix_max', v)} />
          <Field label="Regime VIX Allow Below" value={val('filters', 'regime_vix_allow_below')} defaultVal={dp.filters.regime_vix_allow_below} onChange={v => setEdit('filters', 'regime_vix_allow_below', v)} />
        </div>
        <div style={s.row}>
          <Field label="Regime SMA Period" value={val('filters', 'regime_sma_period')} defaultVal={dp.filters.regime_sma_period} onChange={v => setEdit('filters', 'regime_sma_period', v)} />
        </div>
        <div style={{ marginBottom: '12px' }}>
          <label style={s.label}>Blocked Tiers</label>
          <div style={{ display: 'flex', gap: '12px' }}>
            {[1, 2, 3, 4, 5].map(t => {
              const current = editState.filters?.blocked_tiers || tp.filters.blocked_tiers;
              return (
                <label key={t} style={{ fontSize: '13px', color: '#a0a0c0', cursor: 'pointer' }}>
                  <input type="checkbox" checked={current.includes(t)} onChange={e => {
                    const next = e.target.checked ? [...current, t] : current.filter((x: number) => x !== t);
                    setEdit('filters', 'blocked_tiers', next.sort());
                  }} style={{ marginRight: '4px' }} />
                  Tier {t}
                </label>
              );
            })}
          </div>
        </div>
        <div>
          <button style={s.btn} onClick={() => saveSection('filters')}>Save</button>
          {hasOverride('filters') && <button style={s.btnOutline} onClick={() => resetSection('filters')}>Reset</button>}
        </div>
      </Section>

      {/* 3. Exit Targets */}
      <Section title="Exit Targets" hasOverrides={hasOverride('atr_exits')}>
        <div style={s.row}>
          <Field label="Stop Loss ATR Mult" value={val('atr_exits', 'stop_loss_mult')} defaultVal={dp.atr_exits.stop_loss_mult} onChange={v => setEdit('atr_exits', 'stop_loss_mult', v)} />
          <Field label="T1 ATR Mult" value={val('atr_exits', 't1_target_mult')} defaultVal={dp.atr_exits.t1_target_mult} onChange={v => setEdit('atr_exits', 't1_target_mult', v)} />
          <Field label="T2 ATR Mult" value={val('atr_exits', 't2_target_mult')} defaultVal={dp.atr_exits.t2_target_mult} onChange={v => setEdit('atr_exits', 't2_target_mult', v)} />
          <Field label="T3 ATR Mult" value={val('atr_exits', 't3_target_mult')} defaultVal={dp.atr_exits.t3_target_mult} onChange={v => setEdit('atr_exits', 't3_target_mult', v)} />
        </div>
        <div style={s.row}>
          <Field label="T1 Exit %" value={val('atr_exits', 't1_exit_pct')} defaultVal={dp.atr_exits.t1_exit_pct} onChange={v => setEdit('atr_exits', 't1_exit_pct', v)} />
          <Field label="T2 Exit %" value={val('atr_exits', 't2_exit_pct')} defaultVal={dp.atr_exits.t2_exit_pct} onChange={v => setEdit('atr_exits', 't2_exit_pct', v)} />
          <Field label="T3 Exit %" value={val('atr_exits', 't3_exit_pct')} defaultVal={dp.atr_exits.t3_exit_pct} onChange={v => setEdit('atr_exits', 't3_exit_pct', v)} />
        </div>
        <div style={{ fontSize: '13px', color: exitPctSum === 100 ? '#00d4aa' : '#ff4444', marginBottom: '12px' }}>
          Exit % sum: {exitPctSum} {exitPctSum !== 100 && '(must equal 100)'}
        </div>
        <div>
          <button style={s.btn} onClick={() => saveSection('atr_exits')}>Save</button>
          {hasOverride('atr_exits') && <button style={s.btnOutline} onClick={() => resetSection('atr_exits')}>Reset</button>}
        </div>
      </Section>

      {/* 4. Stop Management */}
      <Section title="Stop Management" hasOverrides={hasOverride('stepped_stops') || hasOverride('breakeven')}>
        <div style={{ marginBottom: '12px' }}>
          <label style={{ fontSize: '13px', color: '#a0a0c0', cursor: 'pointer' }}>
            <input type="checkbox" checked={tp.stepped_stops.enabled} onChange={e => setEdit('stepped_stops', 'enabled', e.target.checked)} style={{ marginRight: '8px' }} />
            Stepped Stops Enabled
          </label>
        </div>
        <div style={s.row}>
          <Field label="Step Size (ATR)" value={val('stepped_stops', 'step_size')} defaultVal={dp.stepped_stops.step_size} onChange={v => setEdit('stepped_stops', 'step_size', v)} />
          <Field label="Delay Days" value={val('stepped_stops', 'delay_days')} defaultVal={dp.stepped_stops.delay_days} onChange={v => setEdit('stepped_stops', 'delay_days', v)} />
        </div>
        <div style={{ marginBottom: '16px' }}>
          <label style={{ fontSize: '13px', color: '#a0a0c0', cursor: 'pointer' }}>
            <input type="checkbox" checked={tp.stepped_stops.use_dynamic_atr} onChange={e => setEdit('stepped_stops', 'use_dynamic_atr', e.target.checked)} style={{ marginRight: '8px' }} />
            Use Dynamic ATR
          </label>
        </div>
        <button style={s.btn} onClick={() => saveSection('stepped_stops')}>Save Stops</button>
        {hasOverride('stepped_stops') && <button style={s.btnOutline} onClick={() => resetSection('stepped_stops')}>Reset</button>}

        <div style={{ borderTop: '1px solid #2a2a4a', marginTop: '16px', paddingTop: '16px' }}>
          <label style={{ ...s.label, marginBottom: '12px' }}>Breakeven Settings</label>
          <div style={s.row}>
            <Field label="Breakeven Offset %" value={val('breakeven', 'offset_pct')} defaultVal={dp.breakeven.offset_pct} onChange={v => setEdit('breakeven', 'offset_pct', v)} />
            <Field label="Cap at T1 Minus %" value={val('breakeven', 'cap_at_t1_minus_pct')} defaultVal={dp.breakeven.cap_at_t1_minus_pct} onChange={v => setEdit('breakeven', 'cap_at_t1_minus_pct', v)} />
          </div>
          <button style={s.btn} onClick={() => saveSection('breakeven')}>Save Breakeven</button>
          {hasOverride('breakeven') && <button style={s.btnOutline} onClick={() => resetSection('breakeven')}>Reset</button>}
        </div>
      </Section>

      {/* 5. Pyramid Rules */}
      <Section title="Pyramid Rules" hasOverrides={hasOverride('pyramid')}>
        <div style={s.row}>
          <Field label="Min V4 Score for Pyramid" value={val('pyramid', 'v4_min_pyramid_score')} defaultVal={dp.pyramid.v4_min_pyramid_score} onChange={v => setEdit('pyramid', 'v4_min_pyramid_score', v)} />
          <Field label="Max Pyramids per Position" value={val('pyramid', 'max_pyramids_per_position')} defaultVal={dp.pyramid.max_pyramids_per_position} onChange={v => setEdit('pyramid', 'max_pyramids_per_position', v)} />
        </div>
        <div>
          <button style={s.btn} onClick={() => saveSection('pyramid')}>Save</button>
          {hasOverride('pyramid') && <button style={s.btnOutline} onClick={() => resetSection('pyramid')}>Reset</button>}
        </div>
      </Section>

      {/* 6. Time Stops */}
      <Section title="Time Stops" hasOverrides={hasOverride('time_stops')}>
        <div style={s.row}>
          <Field label="Stagnant Win Days" value={val('time_stops', 'stagnant_win_days')} defaultVal={dp.time_stops.stagnant_win_days} onChange={v => setEdit('time_stops', 'stagnant_win_days', v)} />
          <Field label="Min Profit % (Stagnant)" value={val('time_stops', 'stagnant_win_min_profit_pct')} defaultVal={dp.time_stops.stagnant_win_min_profit_pct} onChange={v => setEdit('time_stops', 'stagnant_win_min_profit_pct', v)} />
          <Field label="Hard Time Stop Days" value={val('time_stops', 'hard_time_stop_days')} defaultVal={dp.time_stops.hard_time_stop_days} onChange={v => setEdit('time_stops', 'hard_time_stop_days', v)} />
        </div>
        <div>
          <button style={s.btn} onClick={() => saveSection('time_stops')}>Save</button>
          {hasOverride('time_stops') && <button style={s.btnOutline} onClick={() => resetSection('time_stops')}>Reset</button>}
        </div>
      </Section>

      {/* 7. VIX Sizing */}
      <Section title="VIX Sizing" hasOverrides={'vix_sizing_brackets' in overrides.trading_params || 'vix_sizing_multipliers' in overrides.trading_params}>
        <div style={{ fontSize: '13px', color: '#888', marginBottom: '12px' }}>
          VIX brackets and corresponding position size multipliers. Brackets ascending, multipliers descending.
        </div>
        <div style={s.row}>
          <div style={s.field}>
            <label style={s.label}>Brackets (comma-separated)</label>
            <input
              style={s.input}
              value={(editState.vix_sizing_brackets ?? tp.vix_sizing_brackets ?? []).join(', ')}
              placeholder={dp.vix_sizing_brackets.join(', ')}
              onChange={e => {
                const parts = e.target.value.split(',').map(s => Number(s.trim())).filter(n => !isNaN(n));
                setEditState(prev => ({ ...prev, vix_sizing_brackets: parts }));
              }}
            />
          </div>
          <div style={s.field}>
            <label style={s.label}>Multipliers (comma-separated)</label>
            <input
              style={s.input}
              value={(editState.vix_sizing_multipliers ?? tp.vix_sizing_multipliers ?? []).join(', ')}
              placeholder={dp.vix_sizing_multipliers.join(', ')}
              onChange={e => {
                const parts = e.target.value.split(',').map(s => Number(s.trim())).filter(n => !isNaN(n));
                setEditState(prev => ({ ...prev, vix_sizing_multipliers: parts }));
              }}
            />
          </div>
        </div>
        <div>
          <button style={s.btn} onClick={async () => {
            const payload: Record<string, any> = {};
            if (editState.vix_sizing_brackets) payload.vix_sizing_brackets = editState.vix_sizing_brackets;
            if (editState.vix_sizing_multipliers) payload.vix_sizing_multipliers = editState.vix_sizing_multipliers;
            if (Object.keys(payload).length === 0) { showMsg('No changes', 'error'); return; }
            for (const [k, v] of Object.entries(payload)) {
              await saveTopLevel(k, v);
            }
          }}>Save</button>
          {('vix_sizing_brackets' in overrides.trading_params || 'vix_sizing_multipliers' in overrides.trading_params) && (
            <button style={s.btnOutline} onClick={async () => {
              await resetSection('vix_sizing_brackets');
              await resetSection('vix_sizing_multipliers');
              loadConfig();
            }}>Reset</button>
          )}
        </div>
      </Section>

      {/* 8. Stock Universe */}
      <Section title="Stock Universe" hasOverrides={Object.keys(overrides.watchlist).length > 0}>
        <div style={{ overflowX: 'auto' }}>
          <table style={s.table}>
            <thead>
              <tr>
                <th style={s.th}>Symbol</th>
                <th style={s.th}>Sector</th>
                <th style={s.th}>Tier</th>
                <th style={s.th}>V4 Threshold</th>
                <th style={s.th}>Benchmark</th>
                <th style={s.th}>Size Mult</th>
                <th style={s.th}>Status</th>
                <th style={s.th}></th>
              </tr>
            </thead>
            <tbody>
              {stocks.map((stock: any) => {
                const isAdded = addedSymbols.has(stock.symbol);
                const isModified = modifiedSymbols.has(stock.symbol);
                const editing = watchlistEdits[stock.symbol];
                const current = editing || stock;
                return (
                  <tr key={stock.symbol}>
                    <td style={s.td}>
                      <span style={{ fontWeight: 'bold' }}>{stock.symbol}</span>
                    </td>
                    <td style={s.td}>
                      {editing ? <input style={s.tdInput} value={current.sector} onChange={e => setWatchlistEdits(p => ({ ...p, [stock.symbol]: { ...current, sector: e.target.value } }))} /> : stock.sector}
                    </td>
                    <td style={s.td}>
                      {editing ? <input style={{ ...s.tdInput, width: '50px' }} type="number" min={1} max={5} value={current.tier} onChange={e => setWatchlistEdits(p => ({ ...p, [stock.symbol]: { ...current, tier: Number(e.target.value) } }))} /> : stock.tier}
                    </td>
                    <td style={s.td}>
                      {editing ? <input style={{ ...s.tdInput, width: '60px' }} type="number" value={current.v4_threshold} onChange={e => setWatchlistEdits(p => ({ ...p, [stock.symbol]: { ...current, v4_threshold: Number(e.target.value) } }))} /> : stock.v4_threshold}
                    </td>
                    <td style={s.td}>
                      {editing ? (
                        <select style={{ ...s.tdInput, width: '65px' }} value={current.benchmark_index} onChange={e => setWatchlistEdits(p => ({ ...p, [stock.symbol]: { ...current, benchmark_index: e.target.value } }))}>
                          <option value="SPY">SPY</option>
                          <option value="QQQ">QQQ</option>
                        </select>
                      ) : stock.benchmark_index}
                    </td>
                    <td style={s.td}>
                      {editing ? <input style={{ ...s.tdInput, width: '50px' }} type="number" step="0.1" value={current.tier_size_multiplier} onChange={e => setWatchlistEdits(p => ({ ...p, [stock.symbol]: { ...current, tier_size_multiplier: Number(e.target.value) } }))} /> : stock.tier_size_multiplier}
                    </td>
                    <td style={s.td}>
                      {isAdded && <span style={{ ...s.badge, background: '#1a3a1a', color: '#44dd44' }}>Added</span>}
                      {isModified && <span style={{ ...s.badge, background: '#1a2a3a', color: '#3388ff' }}>Modified</span>}
                    </td>
                    <td style={s.td}>
                      {editing ? (
                        <>
                          <button style={{ ...s.btn, padding: '4px 10px', fontSize: '11px' }} onClick={() => { saveStock(current); setWatchlistEdits(p => { const n = { ...p }; delete n[stock.symbol]; return n; }); }}>Save</button>
                          <button style={{ ...s.btnOutline, padding: '4px 10px', fontSize: '11px' }} onClick={() => setWatchlistEdits(p => { const n = { ...p }; delete n[stock.symbol]; return n; })}>Cancel</button>
                        </>
                      ) : (
                        <>
                          <button style={{ ...s.btnOutline, padding: '4px 10px', fontSize: '11px' }} onClick={() => setWatchlistEdits(p => ({ ...p, [stock.symbol]: { ...stock } }))}>Edit</button>
                          <button style={{ ...s.btnDanger, padding: '4px 10px', fontSize: '11px', marginLeft: '4px' }} onClick={() => removeStock(stock.symbol)}>X</button>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div style={{ borderTop: '1px solid #2a2a4a', marginTop: '12px', paddingTop: '12px' }}>
          <label style={{ ...s.label, marginBottom: '8px' }}>Add Stock</label>
          <div style={{ ...s.row, alignItems: 'flex-end' }}>
            <div style={{ ...s.field, minWidth: '80px', maxWidth: '100px' }}>
              <label style={s.label}>Symbol</label>
              <input style={s.input} value={newStock.symbol} onChange={e => setNewStock(p => ({ ...p, symbol: e.target.value.toUpperCase() }))} placeholder="TSLA" maxLength={5} />
            </div>
            <div style={s.field}>
              <label style={s.label}>Sector</label>
              <input style={s.input} value={newStock.sector} onChange={e => setNewStock(p => ({ ...p, sector: e.target.value }))} />
            </div>
            <div style={{ ...s.field, minWidth: '60px', maxWidth: '80px' }}>
              <label style={s.label}>Tier</label>
              <input style={s.input} type="number" min={1} max={5} value={newStock.tier} onChange={e => setNewStock(p => ({ ...p, tier: Number(e.target.value) }))} />
            </div>
            <div style={{ ...s.field, minWidth: '80px', maxWidth: '100px' }}>
              <label style={s.label}>V4 Threshold</label>
              <input style={s.input} type="number" value={newStock.v4_threshold} onChange={e => setNewStock(p => ({ ...p, v4_threshold: Number(e.target.value) }))} />
            </div>
            <div style={{ ...s.field, minWidth: '80px', maxWidth: '100px' }}>
              <label style={s.label}>Benchmark</label>
              <select style={{ ...s.input }} value={newStock.benchmark_index} onChange={e => setNewStock(p => ({ ...p, benchmark_index: e.target.value }))}>
                <option value="SPY">SPY</option>
                <option value="QQQ">QQQ</option>
              </select>
            </div>
            <div style={{ marginBottom: '12px' }}>
              <button style={s.btn} onClick={addStock}>Add</button>
            </div>
          </div>
        </div>
        {Object.keys(overrides.watchlist).length > 0 && (
          <div style={{ marginTop: '8px' }}>
            <button style={s.btnOutline} onClick={() => resetSection('watchlist')}>Reset Watchlist</button>
          </div>
        )}
      </Section>

      {/* 9. Schedule (read-only) */}
      <Section title="Schedule (Read-Only)">
        <div style={s.readOnly}>
          <div><strong>Entry Time:</strong> {tp.entry_time} ET</div>
          <div><strong>Entry Delay:</strong> {tp.entry_delay_days} day(s) after signal</div>
          <div style={{ marginTop: '8px', fontSize: '11px', color: '#666' }}>
            Schedule times are managed by the system scheduler and cannot be changed here.
          </div>
        </div>
      </Section>

      {/* 10. V4 Scoring (read-only) */}
      <Section title="V4 Scoring (Read-Only)">
        <div style={s.readOnly}>
          <div><strong>weight_slope:</strong> 40.9 &nbsp; <strong>weight_adx:</strong> 38.9</div>
          <div><strong>weight_stoch:</strong> 25.8 &nbsp; <strong>weight_rsi:</strong> 22.2</div>
          <div><strong>overbought_stoch_threshold:</strong> 92 &nbsp; <strong>overbought_rsi_threshold:</strong> 80</div>
          <div><strong>extended_penalty:</strong> -0.95</div>
          <div style={{ marginTop: '8px', fontSize: '11px', color: '#666' }}>
            V4 scoring weights are locked algorithm constants and cannot be modified.
          </div>
        </div>
      </Section>

      {/* Reset All */}
      <div style={{ ...s.card, background: '#1a1520', borderColor: '#3a2a3a' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <div style={{ fontSize: '14px', fontWeight: 'bold', color: '#ff6644' }}>Reset All to Defaults</div>
            <div style={{ fontSize: '12px', color: '#666', marginTop: '4px' }}>Remove all customizations and revert to shipped defaults</div>
          </div>
          <button style={s.btnDanger} onClick={resetAll}>Reset All</button>
        </div>
      </div>
    </div>
  );
}
