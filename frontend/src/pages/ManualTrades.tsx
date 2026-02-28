import React, { useState, useEffect } from 'react';
import api from '../services/api';
import TradeConfigModal from '../components/TradeConfigModal';

const s = {
  card: { background: '#1a1a2e', borderRadius: '8px', padding: '16px', border: '1px solid #2a2a4a', marginBottom: '24px' } as React.CSSProperties,
  section: { marginBottom: '32px' },
  sectionTitle: { fontSize: '16px', fontWeight: 'bold', marginBottom: '16px', color: '#a0a0c0' },
  table: { width: '100%', borderCollapse: 'collapse' as const, fontSize: '13px' },
  th: { padding: '8px 12px', textAlign: 'left' as const, borderBottom: '1px solid #2a2a4a', color: '#666', fontWeight: 'normal', fontSize: '11px', textTransform: 'uppercase' as const },
  td: { padding: '8px 12px', borderBottom: '1px solid #1a1a2e' },
  btnAdopt: { background: '#00d4aa', color: '#0f0f1a', border: 'none', padding: '4px 12px', borderRadius: '4px', cursor: 'pointer', fontSize: '12px', fontWeight: 'bold' } as React.CSSProperties,
  btnDismiss: { background: 'none', border: '1px solid #444', color: '#888', padding: '4px 12px', borderRadius: '4px', cursor: 'pointer', fontSize: '12px' } as React.CSSProperties,
  btnEdit: { background: 'none', border: '1px solid #00d4aa', color: '#00d4aa', padding: '4px 12px', borderRadius: '4px', cursor: 'pointer', fontSize: '12px' } as React.CSSProperties,
  btnClose: { background: '#ff4444', color: '#fff', border: 'none', padding: '4px 12px', borderRadius: '4px', cursor: 'pointer', fontSize: '12px' } as React.CSSProperties,
  btnRelease: { background: 'none', border: '1px solid #ffaa00', color: '#ffaa00', padding: '4px 12px', borderRadius: '4px', cursor: 'pointer', fontSize: '12px' } as React.CSSProperties,
  statsBar: { display: 'flex', gap: '24px', padding: '12px 16px', background: '#1a1a2e', borderRadius: '8px', border: '1px solid #2a2a4a', marginBottom: '24px', fontSize: '13px', color: '#a0a0c0' } as React.CSSProperties,
  badge: (mode: string): React.CSSProperties => ({
    fontSize: '10px', padding: '2px 6px', borderRadius: '3px',
    background: mode === 'Ratchet' ? '#00d4aa22' : mode === 'Hold' ? '#ffaa0022' : '#2a2a4a',
    color: mode === 'Ratchet' ? '#00d4aa' : mode === 'Hold' ? '#ffaa00' : '#888',
    marginLeft: '6px',
  }),
};

function pnlColor(val: number) { return val >= 0 ? '#00d4aa' : '#ff4444'; }

function getModeBadge(p: any): string {
  if (!p.targets_enabled) return 'Hold';
  if (p.ratchet_enabled) return 'Ratchet';
  return 'Fixed';
}

export default function ManualTrades() {
  const [orphans, setOrphans] = useState<any[]>([]);
  const [positions, setPositions] = useState<any[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [modalMode, setModalMode] = useState<'adopt' | 'edit'>('adopt');
  const [selectedOrphan, setSelectedOrphan] = useState<any>(null);
  const [selectedTrade, setSelectedTrade] = useState<any>(null);
  const [atr, setAtr] = useState<number | null>(null);
  const [observeOnly, setObserveOnly] = useState(false);

  const loadData = () => {
    api.get('/manual-trades/orphans').then(r => setOrphans(r.data.orphans)).catch(() => {});
    api.get('/dashboard/positions?trade_type=manual').then(r => setPositions(r.data.positions)).catch(() => {});
    api.get('/trades/stats?trade_type=manual').then(r => setStats(r.data)).catch(() => {});
  };

  const loadAll = () => {
    loadData();
    api.get('/settings/').then(r => setObserveOnly(r.data.observe_only)).catch(() => {});
  };

  useEffect(() => {
    loadAll();
    const interval = setInterval(loadAll, 60000);
    return () => clearInterval(interval);
  }, []);

  const handleAdoptClick = (orphan: any) => {
    setSelectedOrphan(orphan);
    setModalMode('adopt');
    setAtr(null);
    setModalOpen(true);
    // Try to fetch ATR
    api.get(`/dashboard/positions`).then(() => {
      // ATR comes from backend during adoption; show modal immediately with loading state
    }).catch(() => {});
  };

  const handleEditClick = (trade: any) => {
    setSelectedTrade(trade);
    setModalMode('edit');
    setAtr(trade.original_atr);
    setModalOpen(true);
  };

  const handleDismiss = async (symbol: string) => {
    if (!window.confirm(`Dismiss orphan ${symbol}? It will reappear if still held on next reconciliation.`)) return;
    try {
      await api.post(`/manual-trades/orphans/${symbol}/dismiss`);
      loadData();
    } catch (e: any) {
      alert(e.response?.data?.detail || 'Dismiss failed');
    }
  };

  const handleAdopt = async (config: any) => {
    try {
      await api.post('/manual-trades/adopt', config);
      setModalOpen(false);
      loadData();
    } catch (e: any) {
      alert(e.response?.data?.detail || 'Adoption failed');
    }
  };

  const handleEdit = async (config: any) => {
    if (!selectedTrade) return;
    try {
      await api.put(`/manual-trades/${selectedTrade.id}/stops`, {
        stop_mode: config.stop_mode,
        stop_value: config.stop_value,
        ratchet_enabled: config.ratchet_enabled,
        ratchet_mode: config.ratchet_mode,
        ratchet_value: config.ratchet_value,
      });
      if (config.targets_enabled) {
        await api.put(`/manual-trades/${selectedTrade.id}/targets`, {
          t1_mode: config.t1_mode, t1_value: config.t1_value,
          t2_mode: config.t2_mode, t2_value: config.t2_value,
          t3_mode: config.t3_mode, t3_value: config.t3_value,
          t1_exit_pct: config.t1_exit_pct, t2_exit_pct: config.t2_exit_pct, t3_exit_pct: config.t3_exit_pct,
        });
      }
      setModalOpen(false);
      loadData();
    } catch (e: any) {
      alert(e.response?.data?.detail || 'Update failed');
    }
  };

  const handleClose = async (tradeId: number, symbol: string) => {
    if (!window.confirm(`CLOSE ${symbol}? This will cancel all orders and market-sell remaining shares. This cannot be undone.`)) return;
    try {
      await api.put(`/manual-trades/${tradeId}/close`);
      loadData();
    } catch (e: any) {
      alert(e.response?.data?.detail || 'Close failed');
    }
  };

  const handleRelease = async (tradeId: number, symbol: string) => {
    if (!window.confirm(`RELEASE ${symbol}? Orders will be cancelled but shares will remain on Tradier (untracked).`)) return;
    try {
      await api.put(`/manual-trades/${tradeId}/release`);
      loadData();
    } catch (e: any) {
      alert(e.response?.data?.detail || 'Release failed');
    }
  };

  const handleHoldToggle = async (tradeId: number, currentlyHold: boolean) => {
    try {
      await api.put(`/manual-trades/${tradeId}/hold-mode`, { enabled: !currentlyHold });
      loadData();
    } catch (e: any) {
      alert(e.response?.data?.detail || 'Hold mode toggle failed');
    }
  };

  return (
    <div>
      <h1 style={{ fontSize: '20px', marginBottom: '24px' }}>Manual Trades</h1>

      {observeOnly && (
        <div style={{ background: '#4488ff22', border: '1px solid #4488ff', borderRadius: '6px', padding: '10px 16px', marginBottom: '16px', color: '#4488ff', fontWeight: 'bold', fontSize: '13px', textAlign: 'center' }}>
          OBSERVE-ONLY MODE — All broker writes are blocked
        </div>
      )}

      {/* Stats bar */}
      {stats && (
        <div style={s.statsBar}>
          <span>{positions.length} open</span>
          <span>{stats.total_trades} closed</span>
          <span style={{ color: pnlColor(stats.total_pnl) }}>{stats.win_rate}% win rate</span>
          <span style={{ color: pnlColor(stats.total_pnl) }}>${stats.total_pnl} total P&L</span>
        </div>
      )}

      {/* Section A: Discovered Orphans */}
      <div style={s.section}>
        <div style={s.sectionTitle}>Discovered Orphans ({orphans.length})</div>
        <div style={s.card}>
          {orphans.length === 0 ? (
            <p style={{ color: '#666', fontSize: '13px' }}>No orphan positions detected. Reconciliation runs every 5 minutes during market hours.</p>
          ) : (
            <div className="table-scroll">
            <table style={s.table}>
              <thead>
                <tr>
                  <th style={s.th}>Symbol</th><th style={s.th}>Shares</th><th style={s.th}>Avg Cost</th><th style={s.th}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {orphans.map((o: any) => (
                  <tr key={o.symbol}>
                    <td style={{ ...s.td, fontWeight: 'bold', color: '#ffaa00' }}>{o.symbol}</td>
                    <td style={s.td}>{o.quantity}</td>
                    <td style={s.td}>${o.cost_basis?.toFixed(2) || '0.00'}</td>
                    <td style={s.td}>
                      <div style={{ display: 'flex', gap: '8px' }}>
                        <button style={{ ...s.btnAdopt, ...(observeOnly ? { opacity: 0.4, cursor: 'not-allowed' } : {}) }} disabled={observeOnly} onClick={() => handleAdoptClick(o)}>Adopt</button>
                        <button style={s.btnDismiss} onClick={() => handleDismiss(o.symbol)}>Dismiss</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
        </div>
      </div>

      {/* Section B: Open Manual Positions */}
      <div style={s.section}>
        <div style={s.sectionTitle}>Open Manual Positions ({positions.length})</div>
        <div style={s.card}>
          {positions.length === 0 ? (
            <p style={{ color: '#666', fontSize: '13px' }}>No manual positions</p>
          ) : (
            <div className="table-scroll">
            <table style={s.table}>
              <thead>
                <tr>
                  <th style={s.th}>Symbol</th><th style={s.th}>Shares</th><th style={s.th}>Entry</th>
                  <th style={s.th}>Stop</th><th style={s.th}>T1</th><th style={s.th}>Mode</th>
                  <th style={s.th}>State</th><th style={s.th}>Days</th><th style={s.th}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p: any) => {
                  const modeBadge = getModeBadge(p);
                  const isHold = !p.targets_enabled;
                  return (
                    <tr key={p.id}>
                      <td style={{ ...s.td, fontWeight: 'bold', color: '#00d4aa' }}>{p.symbol}</td>
                      <td style={s.td}>{p.shares_remaining}/{p.shares}</td>
                      <td style={s.td}>${p.blended_entry_price ?? p.entry_price}</td>
                      <td style={s.td}>${p.stop_price}</td>
                      <td style={s.td}>{p.target_t1 ? `$${p.target_t1}` : '-'}</td>
                      <td style={s.td}>
                        <span style={s.badge(modeBadge)}>{modeBadge}</span>
                      </td>
                      <td style={s.td}>{p.state}</td>
                      <td style={s.td}>{p.days_held ?? '-'}</td>
                      <td style={s.td}>
                        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', ...(observeOnly ? { opacity: 0.4 } : {}) }}>
                          <button style={s.btnEdit} disabled={observeOnly} onClick={() => handleEditClick(p)}>Edit</button>
                          <button
                            style={{ ...s.btnDismiss, color: isHold ? '#00d4aa' : '#ffaa00', borderColor: isHold ? '#00d4aa' : '#ffaa00' }}
                            disabled={observeOnly}
                            onClick={() => handleHoldToggle(p.id, isHold)}
                          >
                            {isHold ? 'Targets' : 'Hold'}
                          </button>
                          <button style={s.btnClose} disabled={observeOnly} onClick={() => handleClose(p.id, p.symbol)}>Close</button>
                          <button style={s.btnRelease} disabled={observeOnly} onClick={() => handleRelease(p.id, p.symbol)}>Release</button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            </div>
          )}
        </div>
      </div>

      {/* Modal */}
      {modalOpen && modalMode === 'adopt' && selectedOrphan && (
        <TradeConfigModal
          mode="adopt"
          symbol={selectedOrphan.symbol}
          shares={selectedOrphan.quantity}
          entryCostCents={selectedOrphan.cost_basis_cents}
          atr={atr || undefined}
          onSubmit={handleAdopt}
          onClose={() => setModalOpen(false)}
        />
      )}
      {modalOpen && modalMode === 'edit' && selectedTrade && (
        <TradeConfigModal
          mode="edit"
          symbol={selectedTrade.symbol}
          shares={selectedTrade.shares_remaining}
          entryCostCents={Math.round((selectedTrade.blended_entry_price || selectedTrade.entry_price) * 100)}
          atr={atr || undefined}
          existingConfig={{
            stop_mode: selectedTrade.stop_mode,
            stop_value: selectedTrade.stop_price,
            ratchet_enabled: selectedTrade.ratchet_enabled,
            ratchet_mode: selectedTrade.ratchet_mode,
            targets_enabled: selectedTrade.targets_enabled,
          }}
          onSubmit={handleEdit}
          onClose={() => setModalOpen(false)}
        />
      )}
    </div>
  );
}
