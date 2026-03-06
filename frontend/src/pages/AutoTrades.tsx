import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import api from '../services/api';

const s = {
  card: { background: '#1a1a2e', borderRadius: '8px', padding: '16px', border: '1px solid #2a2a4a' } as React.CSSProperties,
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '12px', marginBottom: '24px' } as React.CSSProperties,
  statCard: { background: '#1a1a2e', borderRadius: '8px', padding: '12px', border: '1px solid #2a2a4a' } as React.CSSProperties,
  statLabel: { fontSize: '11px', color: '#666', marginBottom: '2px', textTransform: 'uppercase' as const },
  statValue: { fontSize: '18px', fontWeight: 'bold' },
  section: { marginBottom: '32px' },
  sectionTitle: { fontSize: '16px', fontWeight: 'bold', marginBottom: '16px', color: '#a0a0c0' },
  table: { width: '100%', borderCollapse: 'collapse' as const, fontSize: '13px' },
  th: { padding: '8px 12px', textAlign: 'left' as const, borderBottom: '1px solid #2a2a4a', color: '#666', fontWeight: 'normal', fontSize: '11px', textTransform: 'uppercase' as const },
  td: { padding: '8px 12px', borderBottom: '1px solid #1a1a2e' },
};

function pnlColor(val: number) { return val >= 0 ? '#00d4aa' : '#ff4444'; }

export default function AutoTrades() {
  const [positions, setPositions] = useState<any[]>([]);
  const [signals, setSignals] = useState<any[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [orders, setOrders] = useState<Record<string, any[]>>({});
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set());

  const toggleRow = (id: number) => {
    setExpandedRows(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const orderTypeLabel = (t: string) => {
    const map: Record<string, string> = { stop: 'Stop', t1_oco: 'T1 OCO', t2_oco: 'T2 OCO', t3_oco: 'T3 OCO', entry: 'Entry', bracket: 'Bracket' };
    return map[t] || t;
  };

  const loadData = () => {
    api.get('/dashboard/positions?trade_type=auto').then(r => setPositions(r.data.positions)).catch(() => {});
    api.get('/dashboard/signals').then(r => setSignals(r.data.signals)).catch(() => {});
    api.get('/trades/stats?trade_type=auto').then(r => setStats(r.data)).catch(() => {});
    api.get('/dashboard/orders?trade_type=auto').then(r => setOrders(r.data.orders_by_trade)).catch(() => {});
  };

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 60000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <h1 style={{ margin: 0, fontSize: '20px' }}>Auto Trades</h1>
        <Link to="/trades?trade_type=auto" style={{ color: '#00d4aa', fontSize: '13px' }}>View History</Link>
      </div>

      {stats && (
        <div className="stats-grid" style={s.grid}>
          <div style={s.statCard}><div style={s.statLabel}>Total Closed</div><div style={s.statValue}>{stats.total_trades}</div></div>
          <div style={s.statCard}><div style={s.statLabel}>Win Rate</div><div style={{ ...s.statValue, color: '#00d4aa' }}>{stats.win_rate}%</div></div>
          <div style={s.statCard}><div style={s.statLabel}>Profit Factor</div><div style={s.statValue}>{stats.profit_factor}</div></div>
          <div style={s.statCard}><div style={s.statLabel}>Total P&L</div><div style={{ ...s.statValue, color: pnlColor(stats.total_pnl) }}>${stats.total_pnl}</div></div>
        </div>
      )}

      <div style={s.section}>
        <div style={s.sectionTitle}>Open Positions ({positions.length})</div>
        <div style={s.card}>
          {positions.length === 0 ? <p style={{ color: '#666', fontSize: '13px' }}>No open auto positions</p> : (
            <div className="table-scroll">
            <table style={s.table}>
              <thead>
                <tr>
                  <th style={{ ...s.th, width: '32px', padding: '8px 4px' }}></th>
                  <th style={s.th}>Symbol</th><th style={s.th}>Shares</th><th style={s.th}>Entry</th>
                  <th style={s.th}>Price</th><th style={s.th}>Stop</th><th style={s.th}>T1</th><th style={s.th}>T2</th>
                  <th style={s.th}>T3</th><th style={s.th}>State</th><th style={s.th}>Days</th><th style={s.th}>Pyramids</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p: any) => {
                  const tradeOrders = orders[String(p.id)] || [];
                  const hasCoverage = tradeOrders.length > 0;
                  const isExpanded = expandedRows.has(p.id);
                  return (
                  <React.Fragment key={p.id}>
                  <tr style={{ cursor: 'pointer' }} onClick={() => toggleRow(p.id)}>
                    <td style={{ ...s.td, padding: '8px 4px', textAlign: 'center' }}>
                      <span style={{ color: hasCoverage ? '#00d4aa' : '#ff4444', marginRight: '4px', fontSize: '8px' }}>&#9679;</span>
                      <span style={{ color: '#666', fontSize: '11px' }}>{isExpanded ? '\u25BC' : '\u25B6'}</span>
                    </td>
                    <td style={{ ...s.td, fontWeight: 'bold', color: '#00d4aa' }}>{p.symbol}</td>
                    <td style={s.td}>{p.shares_remaining}/{p.shares}</td>
                    <td style={s.td}>${p.blended_entry_price ?? p.entry_price}</td>
                    <td style={{ ...s.td, color: p.current_price && (p.blended_entry_price ?? p.entry_price) ? (p.current_price >= (p.blended_entry_price ?? p.entry_price) ? '#00d4aa' : '#ff4444') : undefined }}>{p.current_price ? `$${p.current_price}` : '-'}</td>
                    <td style={s.td}>${p.stop_price}</td>
                    <td style={{ ...s.td, color: p.t1_filled ? '#00d4aa' : '#666' }}>${p.target_t1}</td>
                    <td style={{ ...s.td, color: p.t2_filled ? '#00d4aa' : '#666' }}>${p.target_t2}</td>
                    <td style={{ ...s.td, color: p.t3_filled ? '#00d4aa' : '#666' }}>${p.target_t3}</td>
                    <td style={s.td}>{p.state}</td>
                    <td style={s.td}>{p.days_held ?? '-'}</td>
                    <td style={s.td}>{p.pyramid_count}</td>
                  </tr>
                  {isExpanded && (
                    <tr>
                      <td colSpan={12} style={{ padding: '0 12px 12px 36px', background: '#12122a' }}>
                        {tradeOrders.length === 0 ? (
                          <div style={{ color: '#ff4444', fontSize: '12px', padding: '8px 0' }}>No open orders — position may be unprotected</div>
                        ) : (
                          <table style={{ ...s.table, fontSize: '12px', marginTop: '4px' }}>
                            <thead>
                              <tr>
                                <th style={{ ...s.th, fontSize: '10px' }}>Type</th>
                                <th style={{ ...s.th, fontSize: '10px' }}>Shares</th>
                                <th style={{ ...s.th, fontSize: '10px' }}>Price</th>
                                <th style={{ ...s.th, fontSize: '10px' }}>Tradier ID</th>
                                <th style={{ ...s.th, fontSize: '10px' }}>Updated</th>
                              </tr>
                            </thead>
                            <tbody>
                              {tradeOrders.map((o: any) => (
                                <tr key={o.order_id}>
                                  <td style={s.td}>{orderTypeLabel(o.order_type)}</td>
                                  <td style={s.td}>{o.shares}</td>
                                  <td style={s.td}>{o.price != null ? `$${o.price}` : '-'}</td>
                                  <td style={{ ...s.td, color: '#666' }}>{o.order_id}</td>
                                  <td style={{ ...s.td, color: '#666' }}>{o.updated_at?.slice(0, 16).replace('T', ' ')}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        )}
                      </td>
                    </tr>
                  )}
                  </React.Fragment>
                  );
                })}
              </tbody>
            </table>
            </div>
          )}
        </div>
      </div>

      <div style={s.section}>
        <div style={s.sectionTitle}>Today's Signals</div>
        <div style={s.card}>
          {signals.length === 0 ? <p style={{ color: '#666', fontSize: '13px' }}>No signals today</p> : (
            <div className="table-scroll">
            <table style={s.table}>
              <thead>
                <tr>
                  <th style={s.th}>Symbol</th><th style={s.th}>V4 Score</th><th style={s.th}>Action</th>
                  <th style={s.th}>Reason</th><th style={s.th}>Entry</th><th style={s.th}>Stop</th>
                  <th style={s.th}>T1</th><th style={s.th}>T2</th><th style={s.th}>T3</th><th style={s.th}>Shares</th>
                </tr>
              </thead>
              <tbody>
                {signals.map((sig: any, i: number) => (
                  <tr key={i}>
                    <td style={{ ...s.td, fontWeight: 'bold' }}>{sig.symbol}</td>
                    <td style={s.td}>{sig.v4_score?.toFixed(1)}</td>
                    <td style={{ ...s.td, color: sig.action === 'buy' ? '#00d4aa' : '#ff4444' }}>{sig.action.toUpperCase()}</td>
                    <td style={{ ...s.td, color: '#666', fontSize: '12px' }}>{sig.skip_reason || '-'}</td>
                    <td style={s.td}>{sig.entry_price ? `$${sig.entry_price}` : '-'}</td>
                    <td style={s.td}>{sig.stop_price ? `$${sig.stop_price}` : '-'}</td>
                    <td style={s.td}>{sig.t1_price ? `$${sig.t1_price}` : '-'}</td>
                    <td style={s.td}>{sig.t2_price ? `$${sig.t2_price}` : '-'}</td>
                    <td style={s.td}>{sig.t3_price ? `$${sig.t3_price}` : '-'}</td>
                    <td style={s.td}>{sig.shares || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
