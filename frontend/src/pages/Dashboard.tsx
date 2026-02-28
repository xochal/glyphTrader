import React, { useState, useEffect } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts';
import api from '../services/api';

const s = {
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '16px', marginBottom: '24px' } as React.CSSProperties,
  card: { background: '#1a1a2e', borderRadius: '8px', padding: '16px', border: '1px solid #2a2a4a' } as React.CSSProperties,
  statLabel: { fontSize: '12px', color: '#666', marginBottom: '4px' },
  statSub: { fontSize: '11px', color: '#555', marginTop: '2px' },
  statValue: { fontSize: '24px', fontWeight: 'bold' },
  section: { marginBottom: '32px' },
  killOn: { background: '#00d4aa', color: '#0f0f1a', border: 'none', padding: '8px 24px', borderRadius: '6px', fontWeight: 'bold', cursor: 'pointer', fontSize: '13px' } as React.CSSProperties,
  killOff: { background: '#ff4444', color: '#fff', border: 'none', padding: '8px 24px', borderRadius: '6px', fontWeight: 'bold', cursor: 'pointer', fontSize: '13px' } as React.CSSProperties,
  observeOn: { background: '#4488ff', color: '#fff', border: 'none', padding: '8px 24px', borderRadius: '6px', fontWeight: 'bold', cursor: 'pointer', fontSize: '13px' } as React.CSSProperties,
  observeOff: { background: 'none', border: '1px solid #4488ff', color: '#4488ff', padding: '8px 24px', borderRadius: '6px', fontWeight: 'bold', cursor: 'pointer', fontSize: '13px' } as React.CSSProperties,
  flattenBtn: { background: '#ff4444', color: '#fff', border: 'none', padding: '12px 24px', borderRadius: '6px', fontWeight: 'bold', cursor: 'pointer', fontSize: '14px', width: '100%', marginBottom: '24px' } as React.CSSProperties,
};

function pnlColor(val: number) { return val >= 0 ? '#00d4aa' : '#ff4444'; }

const equityRanges = [
  { label: '30D', days: 30 },
  { label: '90D', days: 90 },
  { label: '1Y', days: 365 },
  { label: 'ALL', days: 0 },
];

const rangeBtn = (active: boolean): React.CSSProperties => ({
  background: active ? '#00d4aa' : '#1a1a2e',
  color: active ? '#0f0f1a' : '#a0a0c0',
  border: '1px solid #2a2a4a',
  padding: '4px 12px',
  borderRadius: '4px',
  cursor: 'pointer',
  fontSize: '12px',
  fontWeight: active ? 'bold' : 'normal',
});

function regimeBorderColor(type: string) {
  if (type === 'FAVORABLE') return '#00d4aa';
  if (type === 'PANIC') return '#ff4444';
  return '#ffaa00';
}

export default function Dashboard() {
  const [stats, setStats] = useState<any>(null);
  const [equity, setEquity] = useState<any[]>([]);
  const [killSwitch, setKillSwitch] = useState(false);
  const [observeOnly, setObserveOnly] = useState(false);
  const [regime, setRegime] = useState<any>(null);
  const [equityRange, setEquityRange] = useState(90);
  const [flattenStep, setFlattenStep] = useState(0); // 0=hidden, 1=confirm, 2=password, 3=progress
  const [flattenResult, setFlattenResult] = useState<any>(null);

  const loadDashboard = () => {
    api.get('/dashboard/stats').then(r => setStats(r.data)).catch(() => {});
    api.get('/dashboard/regime').then(r => setRegime(r.data)).catch(() => {});
    api.get('/settings/').then(r => {
      setKillSwitch(r.data.trading_enabled);
      setObserveOnly(r.data.observe_only);
    }).catch(() => {});
  };

  useEffect(() => {
    loadDashboard();
    const interval = setInterval(loadDashboard, 60000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    api.get(`/charts/equity?days=${equityRange}`).then(r => setEquity(r.data.data)).catch(() => {});
  }, [equityRange]);

  const toggleKill = async () => {
    if (killSwitch) {
      await api.put('/settings/kill-switch', { enabled: false });
      setKillSwitch(false);
    } else {
      const accepted = window.confirm(
        'DISCLAIMER: This software is provided "as-is" without warranty. ' +
        'It is a personal trading tool, not investment advice. ' +
        'Trading stocks involves substantial risk of loss. ' +
        'You are solely responsible for all trading decisions and their outcomes.\n\n' +
        'Do you understand and accept these terms?'
      );
      if (!accepted) return;
      const pw = window.prompt('Enter password to enable trading:');
      if (!pw) return;
      try {
        await api.put('/settings/kill-switch', { enabled: true, password: pw, disclaimer_accepted: true });
        setKillSwitch(true);
      } catch { alert('Invalid password'); }
    }
  };

  const toggleObserve = async () => {
    const pw = window.prompt(observeOnly ? 'Enter password to disable observe-only mode:' : 'Enter password to enable observe-only mode:');
    if (!pw) return;
    try {
      const r = await api.put('/settings/observe-only', { enabled: !observeOnly, password: pw });
      setObserveOnly(r.data.observe_only);
    } catch { alert('Invalid password'); }
  };

  const handleFlatten = async () => {
    if (flattenStep === 0) {
      setFlattenStep(1);
      return;
    }
    if (flattenStep === 1) {
      const pw = window.prompt('Enter admin password to confirm FLATTEN ALL:');
      if (!pw) { setFlattenStep(0); return; }
      setFlattenStep(3);
      try {
        const r = await api.post('/manual-trades/flatten-all', { password: pw });
        setFlattenResult(r.data);
      } catch (e: any) {
        setFlattenResult({ error: e.response?.data?.detail || 'Flatten failed' });
      }
      setFlattenStep(0);
    }
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <h1 style={{ margin: 0, fontSize: '20px' }}>Dashboard</h1>
        <div style={{ display: 'flex', gap: '12px' }}>
          <button style={observeOnly ? s.observeOn : s.observeOff} onClick={toggleObserve}>
            {observeOnly ? 'OBSERVE ONLY' : 'OBSERVE MODE'}
          </button>
          <button style={killSwitch ? s.killOff : s.killOn} onClick={toggleKill}>
            {killSwitch ? 'DISABLE TRADING' : 'ENABLE TRADING'}
          </button>
        </div>
      </div>

      {observeOnly && (
        <div style={{ background: '#4488ff22', border: '1px solid #4488ff', borderRadius: '6px', padding: '10px 16px', marginBottom: '16px', color: '#4488ff', fontWeight: 'bold', fontSize: '13px', textAlign: 'center' }}>
          OBSERVE-ONLY MODE — All broker writes are blocked
        </div>
      )}

      {regime && (
        <div style={{ ...s.card, borderLeft: `4px solid ${regimeBorderColor(regime.regime_type)}`, marginBottom: '24px', display: 'flex', flexWrap: 'wrap', gap: '24px', alignItems: 'center' }}>
          <div>
            <span style={{ fontSize: '11px', color: '#666', textTransform: 'uppercase' }}>VIX</span>
            <div style={{ fontSize: '18px', fontWeight: 'bold', color: regime.vix_level >= 32 ? '#ff4444' : regime.vix_level >= 19 ? '#ffaa00' : '#00d4aa' }}>
              {regime.vix_level ?? '---'}
            </div>
          </div>
          <div>
            <span style={{ fontSize: '11px', color: '#666', textTransform: 'uppercase' }}>SPY {'>'} SMA100</span>
            <div style={{ fontSize: '18px', fontWeight: 'bold', color: regime.spy_above_sma100 ? '#00d4aa' : '#ff4444' }}>
              {regime.spy_above_sma100 ? 'YES' : 'NO'}
            </div>
          </div>
          <div>
            <span style={{ fontSize: '11px', color: '#666', textTransform: 'uppercase' }}>QQQ {'>'} SMA100</span>
            <div style={{ fontSize: '18px', fontWeight: 'bold', color: regime.qqq_above_sma100 ? '#00d4aa' : '#ff4444' }}>
              {regime.qqq_above_sma100 ? 'YES' : 'NO'}
            </div>
          </div>
          <div style={{ marginLeft: 'auto' }}>
            <span style={{
              padding: '6px 16px',
              borderRadius: '4px',
              fontSize: '13px',
              fontWeight: 'bold',
              background: regime.regime_allows_entry ? '#00d4aa22' : '#ff444422',
              color: regime.regime_allows_entry ? '#00d4aa' : '#ff4444',
              border: `1px solid ${regime.regime_allows_entry ? '#00d4aa' : '#ff4444'}`,
            }}>
              {regime.regime_allows_entry ? 'TRADING ALLOWED' : 'TRADING BLOCKED'}
            </span>
          </div>
        </div>
      )}

      {/* Flatten button — only when kill switch is OFF (trading disabled) and not observe-only */}
      {!killSwitch && !observeOnly && stats?.open_positions > 0 && (
        <div style={{ marginBottom: '24px' }}>
          {flattenStep === 0 && (
            <button style={s.flattenBtn} onClick={handleFlatten}>
              FLATTEN ALL POSITIONS
            </button>
          )}
          {flattenStep === 1 && (
            <div style={{ ...s.card, borderLeft: '4px solid #ff4444' }}>
              <p style={{ color: '#ff4444', fontWeight: 'bold', marginBottom: '8px' }}>
                WARNING: This will sell ALL open positions immediately.
              </p>
              <p style={{ color: '#888', fontSize: '13px', marginBottom: '12px' }}>
                Market orders during market hours, limit orders otherwise.
                This action cannot be undone.
              </p>
              <div style={{ display: 'flex', gap: '12px' }}>
                <button style={{ ...s.flattenBtn, width: 'auto' }} onClick={handleFlatten}>
                  CONFIRM — FLATTEN ALL
                </button>
                <button style={{ ...s.killOn, background: '#333' }} onClick={() => setFlattenStep(0)}>Cancel</button>
              </div>
            </div>
          )}
          {flattenStep === 3 && (
            <div style={{ ...s.card, textAlign: 'center' }}>
              <p style={{ color: '#ffaa00' }}>Flattening positions...</p>
            </div>
          )}
          {flattenResult && (
            <div style={{ ...s.card, marginTop: '8px', borderLeft: flattenResult.error ? '4px solid #ff4444' : '4px solid #00d4aa' }}>
              {flattenResult.error ? (
                <p style={{ color: '#ff4444' }}>{flattenResult.error}</p>
              ) : (
                <div>
                  <p style={{ color: '#00d4aa', fontWeight: 'bold' }}>
                    Flatten complete: {flattenResult.positions_processed} positions processed
                  </p>
                  <p style={{ color: '#888', fontSize: '12px' }}>Mode: {flattenResult.market_mode}</p>
                  {flattenResult.results?.map((r: any, i: number) => (
                    <p key={i} style={{ fontSize: '12px', color: r.status === 'closed' ? '#00d4aa' : r.status === 'error' ? '#ff4444' : '#ffaa00' }}>
                      {r.symbol}: {r.status} {r.order_type ? `(${r.order_type})` : ''} {r.error || ''}
                    </p>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      <div className="stats-grid" style={s.grid}>
        <div style={s.card}>
          <div style={s.statLabel}>Account Value</div>
          <div style={s.statValue}>${stats?.account_value?.toLocaleString() ?? '---'}</div>
        </div>
        <div style={s.card}>
          <div style={s.statLabel}>Daily P&L</div>
          <div style={{ ...s.statValue, color: pnlColor(stats?.daily_pnl ?? 0) }}>${stats?.daily_pnl?.toLocaleString() ?? '---'}</div>
        </div>
        <div style={s.card}>
          <div style={s.statLabel}>Cash Available</div>
          <div style={s.statValue}>${stats?.cash?.toLocaleString() ?? '---'}</div>
        </div>
        <div style={s.card}>
          <div style={s.statLabel}>Open Positions</div>
          <div style={s.statValue}>{stats?.open_positions ?? '---'}</div>
          <div style={s.statSub}>{stats?.open_auto ?? 0} auto / {stats?.open_manual ?? 0} manual</div>
        </div>
        <div style={s.card}>
          <div style={s.statLabel}>Win Rate</div>
          <div style={s.statValue}>{stats?.win_rate ?? '---'}%</div>
        </div>
        <div style={s.card}>
          <div style={s.statLabel}>Total P&L</div>
          <div style={{ ...s.statValue, color: pnlColor(stats?.total_pnl ?? 0) }}>${stats?.total_pnl?.toLocaleString() ?? '---'}</div>
          {stats?.auto_stats && stats?.manual_stats && (
            <div style={s.statSub}>
              A: ${stats.auto_stats.total_pnl?.toLocaleString() ?? 0} / M: ${stats.manual_stats.total_pnl?.toLocaleString() ?? 0}
            </div>
          )}
        </div>
      </div>

      <div style={s.section}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
            <div style={{ fontSize: '16px', fontWeight: 'bold', color: '#a0a0c0' }}>Equity Curve (vs SPY & QQQ)</div>
            <div style={{ display: 'flex', gap: '6px' }}>
              {equityRanges.map(r => (
                <button key={r.days} style={rangeBtn(equityRange === r.days)} onClick={() => setEquityRange(r.days)}>
                  {r.label}
                </button>
              ))}
            </div>
          </div>
        {equity.length > 0 && (
          <div style={{ ...s.card, padding: '16px 8px' }}>
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={equity}>
                <CartesianGrid strokeDasharray="3 3" stroke="#2a2a4a" />
                <XAxis dataKey="date" tick={{ fontSize: 11, fill: '#666' }} />
                <YAxis tick={{ fontSize: 11, fill: '#666' }} unit="%" />
                <Tooltip contentStyle={{ background: '#1a1a2e', border: '1px solid #2a2a4a', fontSize: 12 }} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line type="monotone" dataKey="account_pct" name="Account" stroke="#00d4aa" dot={false} strokeWidth={2} />
                <Line type="monotone" dataKey="spy_pct" name="SPY" stroke="#4488ff" dot={false} strokeWidth={1} />
                <Line type="monotone" dataKey="qqq_pct" name="QQQ" stroke="#ff8844" dot={false} strokeWidth={1} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  );
}
