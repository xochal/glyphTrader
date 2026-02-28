import React, { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import api from '../services/api';

const s = {
  card: { background: '#1a1a2e', borderRadius: '8px', padding: '16px', border: '1px solid #2a2a4a', marginBottom: '24px' } as React.CSSProperties,
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '12px', marginBottom: '24px' } as React.CSSProperties,
  statCard: { background: '#1a1a2e', borderRadius: '8px', padding: '12px', border: '1px solid #2a2a4a' } as React.CSSProperties,
  statLabel: { fontSize: '11px', color: '#666', marginBottom: '2px', textTransform: 'uppercase' as const },
  statValue: { fontSize: '18px', fontWeight: 'bold' },
  table: { width: '100%', borderCollapse: 'collapse' as const, fontSize: '13px' },
  th: { padding: '8px 12px', textAlign: 'left' as const, borderBottom: '1px solid #2a2a4a', color: '#666', fontWeight: 'normal', fontSize: '11px', textTransform: 'uppercase' as const },
  td: { padding: '8px 12px', borderBottom: '1px solid #1a1a2e' },
  filters: { display: 'flex', gap: '12px', marginBottom: '16px', flexWrap: 'wrap' as const },
  select: { background: '#0f0f1a', border: '1px solid #2a2a4a', color: '#e0e0e0', padding: '6px 12px', borderRadius: '4px', fontSize: '13px' },
  input: { background: '#0f0f1a', border: '1px solid #2a2a4a', color: '#e0e0e0', padding: '6px 12px', borderRadius: '4px', fontSize: '13px' },
  heatCell: { padding: '8px 4px', borderRadius: '4px', textAlign: 'center' as const, fontSize: '11px' },
  sectionTitle: { fontSize: '16px', fontWeight: 'bold', marginBottom: '16px', color: '#a0a0c0' },
  typeBadge: (type: string): React.CSSProperties => ({
    fontSize: '10px', padding: '2px 6px', borderRadius: '3px',
    background: type === 'manual' ? '#ffaa0022' : '#2a2a4a',
    color: type === 'manual' ? '#ffaa00' : '#888',
  }),
};

function pnlColor(val: number) { return val >= 0 ? '#00d4aa' : '#ff4444'; }
function heatColor(val: number) {
  if (val > 500) return '#00aa66';
  if (val > 0) return '#004d30';
  if (val === 0) return '#2a2a4a';
  if (val > -500) return '#4d1a1a';
  return '#aa2222';
}

export default function TradeHistory() {
  const [searchParams] = useSearchParams();
  const [trades, setTrades] = useState<any[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [monthly, setMonthly] = useState<any>({});
  const [filter, setFilter] = useState({
    symbol: '',
    result: '',
    trade_type: searchParams.get('trade_type') || '',
  });

  const loadTrades = () => {
    const params: any = {};
    if (filter.symbol) params.symbol = filter.symbol;
    if (filter.result) params.result = filter.result;
    if (filter.trade_type) params.trade_type = filter.trade_type;
    api.get('/trades/history', { params }).then(r => setTrades(r.data.trades)).catch(() => {});
  };

  const loadStats = () => {
    const params: any = {};
    if (filter.trade_type) params.trade_type = filter.trade_type;
    api.get('/trades/stats', { params }).then(r => setStats(r.data)).catch(() => {});
    api.get('/trades/monthly', { params }).then(r => setMonthly(r.data.monthly)).catch(() => {});
  };

  useEffect(() => {
    loadTrades();
    loadStats();
    const interval = setInterval(() => { loadTrades(); loadStats(); }, 60000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => { loadTrades(); loadStats(); }, [filter]);

  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  return (
    <div>
      <h1 style={{ fontSize: '20px', marginBottom: '24px' }}>Trade History</h1>

      {stats && (
        <div className="stats-grid" style={s.grid}>
          <div style={s.statCard}><div style={s.statLabel}>Total Trades</div><div style={s.statValue}>{stats.total_trades}</div></div>
          <div style={s.statCard}><div style={s.statLabel}>Win Rate</div><div style={{ ...s.statValue, color: '#00d4aa' }}>{stats.win_rate}%</div></div>
          <div style={s.statCard}><div style={s.statLabel}>Avg Win</div><div style={{ ...s.statValue, color: '#00d4aa' }}>${stats.avg_win}</div></div>
          <div style={s.statCard}><div style={s.statLabel}>Avg Loss</div><div style={{ ...s.statValue, color: '#ff4444' }}>${stats.avg_loss}</div></div>
          <div style={s.statCard}><div style={s.statLabel}>Profit Factor</div><div style={s.statValue}>{stats.profit_factor}</div></div>
          <div style={s.statCard}><div style={s.statLabel}>Total P&L</div><div style={{ ...s.statValue, color: pnlColor(stats.total_pnl) }}>${stats.total_pnl}</div></div>
        </div>
      )}

      {Object.keys(monthly).length > 0 && (
        <div style={{ marginBottom: '24px' }}>
          <div style={s.sectionTitle}>Monthly Returns</div>
          <div style={s.card}>
            <div className="table-scroll">
            <div style={{ display: 'grid', gridTemplateColumns: 'auto repeat(12, 1fr)', gap: '4px', fontSize: '11px', minWidth: '600px' }}>
              <div></div>
              {months.map(m => <div key={m} style={{ textAlign: 'center', color: '#666' }}>{m}</div>)}
              {(() => {
                const years = [...new Set(Object.keys(monthly).map(k => k.split('-')[0]))].sort();
                return years.map(year => (
                  <React.Fragment key={year}>
                    <div style={{ color: '#666', paddingRight: '8px' }}>{year}</div>
                    {months.map((_, i) => {
                      const key = `${year}-${String(i + 1).padStart(2, '0')}`;
                      const val = monthly[key] || 0;
                      return (
                        <div key={key} style={{ ...s.heatCell, background: heatColor(val), color: val !== 0 ? '#e0e0e0' : '#444' }}>
                          {val !== 0 ? `$${val}` : ''}
                        </div>
                      );
                    })}
                  </React.Fragment>
                ));
              })()}
            </div>
            </div>
          </div>
        </div>
      )}

      <div style={s.filters}>
        <input style={s.input} placeholder="Symbol..." value={filter.symbol} onChange={e => setFilter({ ...filter, symbol: e.target.value })} />
        <select style={s.select} value={filter.result} onChange={e => setFilter({ ...filter, result: e.target.value })}>
          <option value="">All Results</option>
          <option value="win">Wins</option>
          <option value="loss">Losses</option>
        </select>
        <select style={s.select} value={filter.trade_type} onChange={e => setFilter({ ...filter, trade_type: e.target.value })}>
          <option value="">All Types</option>
          <option value="auto">Auto</option>
          <option value="manual">Manual</option>
        </select>
      </div>

      <div style={s.card}>
        {trades.length === 0 ? <p style={{ color: '#666', fontSize: '13px' }}>No closed trades</p> : (
          <div className="table-scroll">
          <table style={s.table}>
            <thead>
              <tr>
                <th style={s.th}>Symbol</th><th style={s.th}>Type</th><th style={s.th}>Entry</th><th style={s.th}>Entry Date</th>
                <th style={s.th}>Exit Date</th><th style={s.th}>P&L</th><th style={s.th}>P&L %</th>
                <th style={s.th}>Days</th><th style={s.th}>Exit Reason</th><th style={s.th}>Pyramids</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t: any) => (
                <tr key={t.id}>
                  <td style={{ ...s.td, fontWeight: 'bold' }}>{t.symbol}</td>
                  <td style={s.td}><span style={s.typeBadge(t.trade_type)}>{t.trade_type === 'manual' ? 'M' : 'A'}</span></td>
                  <td style={s.td}>${t.entry_price}</td>
                  <td style={{ ...s.td, fontSize: '12px' }}>{t.entry_time?.split('T')[0] || '-'}</td>
                  <td style={{ ...s.td, fontSize: '12px' }}>{t.close_time?.split('T')[0] || '-'}</td>
                  <td style={{ ...s.td, color: pnlColor(t.pnl), fontWeight: 'bold' }}>${t.pnl}</td>
                  <td style={{ ...s.td, color: pnlColor(t.pnl_pct) }}>{t.pnl_pct}%</td>
                  <td style={s.td}>{t.hold_days ?? '-'}</td>
                  <td style={{ ...s.td, fontSize: '12px', color: '#666' }}>{t.exit_reason}</td>
                  <td style={s.td}>{t.pyramid_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </div>
    </div>
  );
}
