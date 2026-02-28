import React, { useState, useEffect } from 'react';

const s = {
  overlay: { position: 'fixed' as const, top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.7)', display: 'flex', justifyContent: 'center', alignItems: 'center', zIndex: 1000 },
  modal: { background: '#1a1a2e', border: '1px solid #2a2a4a', borderRadius: '12px', padding: '24px', width: '100%', maxWidth: '520px', maxHeight: '90vh', overflowY: 'auto' as const },
  title: { fontSize: '18px', fontWeight: 'bold', marginBottom: '20px', color: '#e0e0e0' },
  row: { display: 'flex', gap: '12px', marginBottom: '12px', alignItems: 'center' } as React.CSSProperties,
  label: { fontSize: '12px', color: '#888', marginBottom: '4px' },
  input: { background: '#0f0f1a', border: '1px solid #2a2a4a', color: '#e0e0e0', padding: '8px 12px', borderRadius: '4px', fontSize: '13px', width: '100%' } as React.CSSProperties,
  select: { background: '#0f0f1a', border: '1px solid #2a2a4a', color: '#e0e0e0', padding: '8px 12px', borderRadius: '4px', fontSize: '13px' } as React.CSSProperties,
  preview: { fontSize: '12px', color: '#00d4aa', marginTop: '2px' },
  section: { marginBottom: '16px', padding: '12px', background: '#0f0f1a', borderRadius: '8px' },
  sectionTitle: { fontSize: '13px', fontWeight: 'bold', color: '#a0a0c0', marginBottom: '8px' },
  checkbox: { marginRight: '8px' },
  btnPrimary: { background: '#00d4aa', color: '#0f0f1a', border: 'none', padding: '10px 24px', borderRadius: '6px', fontWeight: 'bold', cursor: 'pointer', fontSize: '14px' } as React.CSSProperties,
  btnCancel: { background: 'none', border: '1px solid #444', color: '#a0a0c0', padding: '10px 24px', borderRadius: '6px', cursor: 'pointer', fontSize: '14px' } as React.CSSProperties,
};

interface Props {
  mode: 'adopt' | 'edit';
  symbol: string;
  shares?: number;
  entryCostCents?: number;
  atr?: number;
  existingConfig?: any;
  onSubmit: (config: any) => void;
  onClose: () => void;
}

function calcPrice(entry: number, mode: string, value: number, atr: number, direction: 'below' | 'above'): number {
  if (mode === 'atr') return direction === 'below' ? entry - value * atr * 100 : entry + value * atr * 100;
  if (mode === 'dollar') return direction === 'below' ? entry - value * 100 : entry + value * 100;
  if (mode === 'percent') return direction === 'below' ? entry * (1 - value / 100) : entry * (1 + value / 100);
  return entry;
}

export default function TradeConfigModal({ mode, symbol, shares: initShares, entryCostCents, atr, existingConfig, onSubmit, onClose }: Props) {
  const [entryPrice, setEntryPrice] = useState(entryCostCents ? (entryCostCents / 100).toString() : '');
  const [shares, setShares] = useState(initShares?.toString() || '');
  const [stopMode, setStopMode] = useState(existingConfig?.stop_mode || 'atr');
  const [stopValue, setStopValue] = useState(existingConfig?.stop_value?.toString() || '5');
  const [ratchetEnabled, setRatchetEnabled] = useState(existingConfig?.ratchet_enabled || false);
  const [ratchetMode, setRatchetMode] = useState(existingConfig?.ratchet_mode || 'atr');
  const [ratchetValue, setRatchetValue] = useState(existingConfig?.ratchet_value?.toString() || '3');
  const [holdMode, setHoldMode] = useState(existingConfig?.targets_enabled === false || existingConfig?.targets_enabled === 0);
  const [t1Mode, setT1Mode] = useState(existingConfig?.t1_mode || 'atr');
  const [t1Value, setT1Value] = useState(existingConfig?.t1_value?.toString() || '10');
  const [t2Mode, setT2Mode] = useState(existingConfig?.t2_mode || 'atr');
  const [t2Value, setT2Value] = useState(existingConfig?.t2_value?.toString() || '15');
  const [t3Mode, setT3Mode] = useState(existingConfig?.t3_mode || 'atr');
  const [t3Value, setT3Value] = useState(existingConfig?.t3_value?.toString() || '20');
  const [t1Pct, setT1Pct] = useState(existingConfig?.t1_exit_pct?.toString() || '70');
  const [t2Pct, setT2Pct] = useState(existingConfig?.t2_exit_pct?.toString() || '20');
  const [t3Pct, setT3Pct] = useState(existingConfig?.t3_exit_pct?.toString() || '10');

  const entry = parseFloat(entryPrice) * 100 || 0;
  const atrVal = atr || 0;
  const stopPreview = atrVal > 0 && entry > 0 ? (calcPrice(entry, stopMode, parseFloat(stopValue) || 0, atrVal, 'below') / 100).toFixed(2) : '...';
  const t1Preview = atrVal > 0 && entry > 0 ? (calcPrice(entry, t1Mode, parseFloat(t1Value) || 0, atrVal, 'above') / 100).toFixed(2) : '...';
  const t2Preview = atrVal > 0 && entry > 0 ? (calcPrice(entry, t2Mode, parseFloat(t2Value) || 0, atrVal, 'above') / 100).toFixed(2) : '...';
  const t3Preview = atrVal > 0 && entry > 0 ? (calcPrice(entry, t3Mode, parseFloat(t3Value) || 0, atrVal, 'above') / 100).toFixed(2) : '...';

  const pctSum = (parseInt(t1Pct) || 0) + (parseInt(t2Pct) || 0) + (parseInt(t3Pct) || 0);

  const handleSubmit = () => {
    const config: any = {
      symbol,
      shares: parseInt(shares),
      entry_price_cents: Math.round(parseFloat(entryPrice) * 100),
      stop_mode: stopMode,
      stop_value: parseFloat(stopValue),
      ratchet_enabled: ratchetEnabled,
      ratchet_mode: ratchetEnabled ? ratchetMode : null,
      ratchet_value: ratchetEnabled ? parseFloat(ratchetValue) : null,
      targets_enabled: !holdMode,
      t1_mode: t1Mode,
      t1_value: parseFloat(t1Value),
      t2_mode: t2Mode,
      t2_value: parseFloat(t2Value),
      t3_mode: t3Mode,
      t3_value: parseFloat(t3Value),
      t1_exit_pct: parseInt(t1Pct),
      t2_exit_pct: parseInt(t2Pct),
      t3_exit_pct: parseInt(t3Pct),
    };
    onSubmit(config);
  };

  const modeSelect = (val: string, onChange: (v: string) => void) => (
    <select style={s.select} value={val} onChange={e => onChange(e.target.value)}>
      <option value="atr">ATR x</option>
      <option value="dollar">Dollar</option>
      <option value="percent">Percent</option>
    </select>
  );

  return (
    <div style={s.overlay} onClick={onClose}>
      <div style={s.modal} onClick={e => e.stopPropagation()}>
        <div style={s.title}>{mode === 'adopt' ? `Adopt ${symbol}` : `Edit ${symbol}`}</div>

        {atrVal > 0 && <div style={{ fontSize: '12px', color: '#888', marginBottom: '12px' }}>ATR: ${atrVal.toFixed(2)}</div>}
        {!atrVal && <div style={{ fontSize: '12px', color: '#ffaa00', marginBottom: '12px' }}>Loading ATR...</div>}

        {/* Entry price + shares (adopt mode) */}
        {mode === 'adopt' && (
          <div style={s.section}>
            <div style={s.sectionTitle}>Position</div>
            <div style={s.row}>
              <div style={{ flex: 1 }}>
                <div style={s.label}>Entry Price ($)</div>
                <input style={s.input} type="number" step="0.01" value={entryPrice} onChange={e => setEntryPrice(e.target.value)} />
              </div>
              <div style={{ flex: 1 }}>
                <div style={s.label}>Shares</div>
                <input style={s.input} type="number" value={shares} onChange={e => setShares(e.target.value)} />
              </div>
            </div>
          </div>
        )}

        {/* Stop config */}
        <div style={s.section}>
          <div style={s.sectionTitle}>Stop Loss</div>
          <div style={s.row}>
            {modeSelect(stopMode, setStopMode)}
            <input style={{ ...s.input, width: '80px' }} type="number" step="0.1" value={stopValue} onChange={e => setStopValue(e.target.value)} />
          </div>
          <div style={s.preview}>Stop: ${stopPreview}</div>

          {/* Ratchet */}
          <div style={{ marginTop: '12px' }}>
            <label style={{ fontSize: '13px', cursor: 'pointer' }}>
              <input type="checkbox" style={s.checkbox} checked={ratchetEnabled} onChange={e => setRatchetEnabled(e.target.checked)} />
              Enable trailing ratchet
            </label>
            {ratchetEnabled && (
              <div style={{ ...s.row, marginTop: '8px' }}>
                {modeSelect(ratchetMode, setRatchetMode)}
                <input style={{ ...s.input, width: '80px' }} type="number" step="0.1" value={ratchetValue} onChange={e => setRatchetValue(e.target.value)} />
              </div>
            )}
          </div>
        </div>

        {/* Hold mode toggle */}
        <div style={{ marginBottom: '12px' }}>
          <label style={{ fontSize: '13px', cursor: 'pointer' }}>
            <input type="checkbox" style={s.checkbox} checked={holdMode} onChange={e => setHoldMode(e.target.checked)} />
            Hold Mode (disable targets, keep stop only)
          </label>
        </div>

        {/* Target config */}
        {!holdMode && (
          <div style={s.section}>
            <div style={s.sectionTitle}>Targets</div>
            {[
              { label: 'T1', mode: t1Mode, setMode: setT1Mode, value: t1Value, setValue: setT1Value, pct: t1Pct, setPct: setT1Pct, preview: t1Preview },
              { label: 'T2', mode: t2Mode, setMode: setT2Mode, value: t2Value, setValue: setT2Value, pct: t2Pct, setPct: setT2Pct, preview: t2Preview },
              { label: 'T3', mode: t3Mode, setMode: setT3Mode, value: t3Value, setValue: setT3Value, pct: t3Pct, setPct: setT3Pct, preview: t3Preview },
            ].map(t => (
              <div key={t.label} style={{ marginBottom: '8px' }}>
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                  <span style={{ width: '24px', fontSize: '12px', color: '#888' }}>{t.label}</span>
                  {modeSelect(t.mode, t.setMode)}
                  <input style={{ ...s.input, width: '70px' }} type="number" step="0.1" value={t.value} onChange={e => t.setValue(e.target.value)} />
                  <input style={{ ...s.input, width: '50px' }} type="number" value={t.pct} onChange={e => t.setPct(e.target.value)} placeholder="%" />
                  <span style={{ fontSize: '11px', color: '#666' }}>%</span>
                </div>
                <div style={s.preview}>{t.label}: ${t.preview}</div>
              </div>
            ))}
            <div style={{ fontSize: '12px', color: pctSum === 100 ? '#00d4aa' : '#ff4444', marginTop: '4px' }}>
              Exit split: {pctSum}% {pctSum !== 100 && '(must equal 100%)'}
            </div>
          </div>
        )}

        <div style={{ display: 'flex', gap: '12px', justifyContent: 'flex-end', marginTop: '20px' }}>
          <button style={s.btnCancel} onClick={onClose}>Cancel</button>
          <button
            style={{ ...s.btnPrimary, opacity: (!holdMode && pctSum !== 100) ? 0.5 : 1 }}
            onClick={handleSubmit}
            disabled={!holdMode && pctSum !== 100}
          >
            {mode === 'adopt' ? 'Adopt Position' : 'Save Changes'}
          </button>
        </div>
      </div>
    </div>
  );
}
