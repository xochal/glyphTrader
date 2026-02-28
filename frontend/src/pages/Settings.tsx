import React, { useState, useEffect } from 'react';
import api from '../services/api';

const s = {
  card: { background: '#1a1a2e', borderRadius: '8px', padding: '20px', border: '1px solid #2a2a4a', marginBottom: '20px' } as React.CSSProperties,
  sectionTitle: { fontSize: '15px', fontWeight: 'bold', marginBottom: '16px', color: '#a0a0c0' },
  label: { display: 'block', fontSize: '12px', color: '#666', marginBottom: '4px', textTransform: 'uppercase' as const },
  input: { width: '100%', padding: '8px 12px', background: '#0f0f1a', border: '1px solid #2a2a4a', borderRadius: '6px', color: '#e0e0e0', fontSize: '14px', marginBottom: '12px', boxSizing: 'border-box' as const },
  select: { width: '100%', padding: '8px 12px', background: '#0f0f1a', border: '1px solid #2a2a4a', borderRadius: '6px', color: '#e0e0e0', fontSize: '14px', marginBottom: '12px' },
  btn: { padding: '8px 20px', background: '#00d4aa', color: '#0f0f1a', border: 'none', borderRadius: '6px', fontSize: '13px', fontWeight: 'bold', cursor: 'pointer', marginRight: '8px' } as React.CSSProperties,
  btnDanger: { padding: '8px 20px', background: '#ff4444', color: '#fff', border: 'none', borderRadius: '6px', fontSize: '13px', fontWeight: 'bold', cursor: 'pointer' } as React.CSSProperties,
  btnOutline: { padding: '8px 20px', background: 'transparent', color: '#a0a0c0', border: '1px solid #444', borderRadius: '6px', fontSize: '13px', cursor: 'pointer', marginRight: '8px' } as React.CSSProperties,
  statusRow: { display: 'flex', justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid #1a1a2e', fontSize: '13px' } as React.CSSProperties,
  msg: { fontSize: '13px', padding: '8px', borderRadius: '4px', marginBottom: '12px' },
};

export default function Settings() {
  const [settings, setSettings] = useState<any>(null);
  const [token, setToken] = useState('');
  const [account, setAccount] = useState('');
  const [env, setEnv] = useState('sandbox');
  const [msg, setMsg] = useState({ text: '', type: '' });
  const [systemStatus, setSystemStatus] = useState<any>(null);
  const [updates, setUpdates] = useState<any>(null);
  const [auditLog, setAuditLog] = useState<any[]>([]);
  const [currentPw, setCurrentPw] = useState('');
  const [newPw, setNewPw] = useState('');
  const [confirmPw, setConfirmPw] = useState('');
  const [licenseKey, setLicenseKey] = useState('');
  const [licenseStatus, setLicenseStatus] = useState<any>(null);

  const loadSettings = () => {
    api.get('/settings/').then(r => {
      setSettings(r.data);
      setEnv(r.data.tradier_environment);
      setAccount(r.data.tradier_account || '');
    }).catch(() => {});
    api.get('/settings/system-status').then(r => setSystemStatus(r.data)).catch(() => {});
    api.get('/settings/license').then(r => setLicenseStatus(r.data)).catch(() => {});
    api.get('/settings/audit-log?limit=20').then(r => setAuditLog(r.data.events)).catch(() => {});
  };

  useEffect(() => {
    loadSettings();
    const interval = setInterval(loadSettings, 60000);
    return () => clearInterval(interval);
  }, []);

  const saveCredentials = async () => {
    setMsg({ text: '', type: '' });
    const accepted = window.confirm(
      'DISCLAIMER: This software is provided "as-is" without warranty. ' +
      'It is a personal trading tool, not investment advice. ' +
      'Trading stocks involves substantial risk of loss. ' +
      'You are solely responsible for all trading decisions and their outcomes.\n\n' +
      'Do you understand and accept these terms?'
    );
    if (!accepted) return;
    try {
      await api.put('/settings/credentials', { tradier_token: token, tradier_account: account, tradier_environment: env, disclaimer_accepted: true });
      setMsg({ text: 'Credentials saved', type: 'success' });
      setToken('');
    } catch (err: any) {
      setMsg({ text: err.response?.data?.detail || 'Failed to save', type: 'error' });
    }
  };

  const testConnection = async () => {
    setMsg({ text: 'Testing...', type: '' });
    try {
      const r = await api.post('/settings/test-connection');
      if (r.data.connected) {
        setMsg({ text: `Connected! Account: ${r.data.account_type}, Equity: $${r.data.total_equity}`, type: 'success' });
      } else {
        setMsg({ text: `Connection failed: ${r.data.error}`, type: 'error' });
      }
    } catch (err: any) {
      setMsg({ text: err.response?.data?.detail || 'Test failed', type: 'error' });
    }
  };

  const revokeAll = async () => {
    if (!window.confirm('Revoke ALL sessions? You will need to log in again.')) return;
    try {
      await api.post('/auth/revoke-all');
      setMsg({ text: 'All sessions revoked', type: 'success' });
    } catch {
      setMsg({ text: 'Failed to revoke sessions', type: 'error' });
    }
  };

  const changePassword = async () => {
    setMsg({ text: '', type: '' });
    if (newPw !== confirmPw) { setMsg({ text: 'Passwords do not match', type: 'error' }); return; }
    if (newPw.length < 8) { setMsg({ text: 'Password must be at least 8 characters', type: 'error' }); return; }
    try {
      await api.put('/settings/password', { current_password: currentPw, new_password: newPw });
      setMsg({ text: 'Password changed successfully', type: 'success' });
      setCurrentPw(''); setNewPw(''); setConfirmPw('');
    } catch (err: any) {
      setMsg({ text: err.response?.data?.detail || 'Failed to change password', type: 'error' });
    }
  };

  const checkUpdates = async () => {
    try {
      const r = await api.get('/settings/check-updates');
      setUpdates(r.data);
    } catch {
      setUpdates({ error: 'Check failed' });
    }
  };

  const activateLicense = async () => {
    setMsg({ text: '', type: '' });
    if (!licenseKey.trim()) { setMsg({ text: 'Enter a license key', type: 'error' }); return; }
    try {
      await api.put('/settings/license', { key: licenseKey.trim() });
      setMsg({ text: 'Production license activated', type: 'success' });
      setLicenseKey('');
      loadSettings();
    } catch (err: any) {
      setMsg({ text: err.response?.data?.detail || 'Invalid license key', type: 'error' });
    }
  };

  const removeLicense = async () => {
    if (!window.confirm('Remove production license? Environment will be reset to sandbox.')) return;
    setMsg({ text: '', type: '' });
    try {
      await api.delete('/settings/license');
      setMsg({ text: 'License removed, environment set to sandbox', type: 'success' });
      setEnv('sandbox');
      loadSettings();
    } catch (err: any) {
      setMsg({ text: err.response?.data?.detail || 'Failed to remove license', type: 'error' });
    }
  };

  return (
    <div>
      <h1 style={{ fontSize: '20px', marginBottom: '24px' }}>Settings</h1>

      {msg.text && (
        <div style={{ ...s.msg, background: msg.type === 'error' ? '#2a1a1a' : '#1a2a1a', color: msg.type === 'error' ? '#ff4444' : '#00d4aa', border: `1px solid ${msg.type === 'error' ? '#441a1a' : '#1a441a'}` }}>
          {msg.text}
        </div>
      )}

      <div style={s.card}>
        <div style={s.sectionTitle}>Tradier Credentials</div>
        {settings?.system_locked && (
          <div style={{ ...s.msg, background: '#2a2a1a', color: '#ffaa44', border: '1px solid #44441a' }}>
            System is locked. Log in to unlock encryption.
          </div>
        )}
        <label style={s.label}>API Token {settings?.tradier_token_last4 && `(current: ****${settings.tradier_token_last4})`}</label>
        <input style={s.input} type="password" value={token} onChange={e => setToken(e.target.value)} placeholder="Enter new API token" />
        <label style={s.label}>Account Number</label>
        <input style={s.input} type="text" value={account} onChange={e => setAccount(e.target.value)} placeholder="Account number" />
        <label style={s.label}>Environment</label>
        <select style={s.select} value={env} onChange={e => setEnv(e.target.value)}>
          <option value="sandbox">Sandbox</option>
          <option value="production" disabled={!licenseStatus?.licensed}>Production{!licenseStatus?.licensed ? ' (license required)' : ''}</option>
        </select>
        <div>
          <button style={s.btn} onClick={saveCredentials}>Save Credentials</button>
          <button style={s.btnOutline} onClick={testConnection}>Test Connection</button>
        </div>
      </div>

      <div style={s.card}>
        <div style={s.sectionTitle}>Production License</div>
        <div style={{ fontSize: '12px', color: '#888', marginBottom: '12px' }}>
          A valid license key is required to connect to a production Tradier account.
          Keys are version-locked and must be reissued after each update.
        </div>
        {licenseStatus?.licensed ? (
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
              <span style={{ color: '#00d4aa', fontSize: '13px', fontWeight: 'bold' }}>LICENSED</span>
              <span style={{ color: '#666', fontSize: '12px' }}>v{licenseStatus.current_version}</span>
            </div>
            <button style={s.btnDanger} onClick={removeLicense}>Remove License</button>
          </div>
        ) : (
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
              <span style={{ color: '#ffaa00', fontSize: '13px', fontWeight: 'bold' }}>UNLICENSED</span>
              <span style={{ color: '#666', fontSize: '12px' }}>Paper trading only</span>
            </div>
            {licenseStatus?.has_key && !licenseStatus?.version_match && (
              <div style={{ ...s.msg, background: '#2a2a1a', color: '#ffaa44', border: '1px solid #44441a', marginBottom: '12px' }}>
                License key is for v{licenseStatus.stored_version} but current version is v{licenseStatus.current_version}. Enter a new key.
              </div>
            )}
            <input
              style={s.input}
              type="text"
              value={licenseKey}
              onChange={e => setLicenseKey(e.target.value)}
              placeholder="GT-..."
              spellCheck={false}
            />
            <button style={s.btn} onClick={activateLicense}>Activate License</button>
          </div>
        )}
      </div>

      <div style={s.card}>
        <div style={s.sectionTitle}>Change Password</div>
        <label style={s.label}>Current Password</label>
        <input style={s.input} type="password" value={currentPw} onChange={e => setCurrentPw(e.target.value)} />
        <label style={s.label}>New Password</label>
        <input style={s.input} type="password" value={newPw} onChange={e => setNewPw(e.target.value)} />
        <label style={s.label}>Confirm New Password</label>
        <input style={s.input} type="password" value={confirmPw} onChange={e => setConfirmPw(e.target.value)} />
        <button style={s.btn} onClick={changePassword}>Change Password</button>
      </div>

      <div style={s.card}>
        <div style={s.sectionTitle}>Security</div>
        <button style={s.btnDanger} onClick={revokeAll}>Revoke All Sessions</button>
      </div>

      <div style={s.card}>
        <div style={s.sectionTitle}>System Status</div>
        <div style={s.statusRow}><span style={{ color: '#666' }}>Version</span><span>{systemStatus?.version || '---'}</span></div>
        <div style={s.statusRow}><span style={{ color: '#666' }}>System</span><span style={{ color: systemStatus?.locked ? '#ff4444' : '#00d4aa' }}>{systemStatus?.locked ? 'LOCKED' : 'UNLOCKED'}</span></div>
        <div style={s.statusRow}><span style={{ color: '#666' }}>Trading</span><span style={{ color: settings?.trading_enabled ? '#00d4aa' : '#ff4444' }}>{settings?.trading_enabled ? 'ENABLED' : 'DISABLED'}</span></div>
        <div style={s.statusRow}><span style={{ color: '#666' }}>Observe Only</span><span style={{ color: settings?.observe_only ? '#4488ff' : '#666' }}>{settings?.observe_only ? 'ACTIVE' : 'OFF'}</span></div>
        <div style={s.statusRow}><span style={{ color: '#666' }}>Scheduler</span><span style={{ color: systemStatus?.scheduler?.running ? '#00d4aa' : '#ff4444' }}>{systemStatus?.scheduler?.running ? 'Running' : 'Stopped'}</span></div>
        {systemStatus?.scheduler?.jobs?.map((job: any) => (
          <div key={job.id} style={s.statusRow}>
            <span style={{ color: '#888', fontSize: '12px' }}>{job.id}</span>
            <span style={{ fontSize: '12px' }}>
              {job.last_run ? `Last: ${new Date(job.last_run).toLocaleTimeString()}` : 'Never'}
              {job.next_run ? ` | Next: ${new Date(job.next_run).toLocaleTimeString()}` : ''}
            </span>
          </div>
        ))}
        <div style={{ marginTop: '12px' }}>
          <button style={s.btnOutline} onClick={checkUpdates}>Check for Updates</button>
          {updates && (
            <span style={{ fontSize: '13px', marginLeft: '12px', color: updates.update_available ? '#ffaa44' : '#00d4aa' }}>
              {updates.error || (updates.update_available ? `Update available (${updates.commits_behind} commits behind)` : 'Up to date')}
            </span>
          )}
        </div>
      </div>

      <div style={s.card}>
        <div style={s.sectionTitle}>Audit Log</div>
        {auditLog.length === 0 ? <p style={{ color: '#666', fontSize: '13px' }}>No events</p> : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
            <thead>
              <tr>
                <th style={{ ...s.label, padding: '4px 8px', textAlign: 'left' }}>Time</th>
                <th style={{ ...s.label, padding: '4px 8px', textAlign: 'left' }}>Event</th>
                <th style={{ ...s.label, padding: '4px 8px', textAlign: 'left' }}>IP</th>
              </tr>
            </thead>
            <tbody>
              {auditLog.map((e: any) => (
                <tr key={e.id}>
                  <td style={{ padding: '4px 8px', color: '#666' }}>{new Date(e.created_at).toLocaleString()}</td>
                  <td style={{ padding: '4px 8px' }}>{e.event_type}</td>
                  <td style={{ padding: '4px 8px', color: '#666' }}>{e.ip_address}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
