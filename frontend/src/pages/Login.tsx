import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { login, setup, checkAuthStatus } from '../services/auth';

const styles = {
  container: { display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh', background: '#0f0f1a' } as React.CSSProperties,
  card: { background: '#1a1a2e', padding: '40px', borderRadius: '12px', width: '380px', border: '1px solid #2a2a4a' } as React.CSSProperties,
  title: { fontSize: '28px', fontWeight: 'bold', color: '#00d4aa', marginBottom: '8px', textAlign: 'center' as const },
  subtitle: { fontSize: '13px', color: '#666', marginBottom: '32px', textAlign: 'center' as const },
  label: { display: 'block', fontSize: '13px', color: '#a0a0c0', marginBottom: '6px' },
  input: { width: '100%', padding: '10px 12px', background: '#0f0f1a', border: '1px solid #2a2a4a', borderRadius: '6px', color: '#e0e0e0', fontSize: '14px', marginBottom: '16px', boxSizing: 'border-box' as const },
  button: { width: '100%', padding: '12px', background: '#00d4aa', color: '#0f0f1a', border: 'none', borderRadius: '6px', fontSize: '15px', fontWeight: 'bold', cursor: 'pointer' },
  error: { color: '#ff4444', fontSize: '13px', marginBottom: '12px', textAlign: 'center' as const },
  success: { color: '#00d4aa', fontSize: '13px', marginBottom: '12px', textAlign: 'center' as const },
  recoveryBox: { background: '#1a0f0f', border: '1px solid #aa4400', borderRadius: '8px', padding: '16px', marginTop: '16px' },
  recoveryTitle: { color: '#ff8844', fontSize: '14px', fontWeight: 'bold', marginBottom: '8px' },
  recoveryKey: { fontFamily: 'monospace', fontSize: '12px', color: '#ffaa66', wordBreak: 'break-all' as const, background: '#0f0a05', padding: '8px', borderRadius: '4px' },
};

export default function Login() {
  const navigate = useNavigate();
  const [isSetup, setIsSetup] = useState(false);
  const [loading, setLoading] = useState(true);
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [setupToken, setSetupToken] = useState('');
  const [error, setError] = useState('');
  const [recoveryKey, setRecoveryKey] = useState('');
  const [showTokenField, setShowTokenField] = useState(false);

  useEffect(() => {
    checkAuthStatus().then((status) => {
      setIsSetup(!status.setup_complete);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    try {
      await login(password);
      navigate('/');
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Login failed');
    }
  };

  const handleSetup = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    if (password !== confirmPassword) { setError('Passwords do not match'); return; }
    if (password.length < 8) { setError('Password must be at least 8 characters'); return; }
    try {
      const result = await setup(setupToken, password);
      setRecoveryKey(result.recovery_key);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Setup failed');
    }
  };

  if (loading) return <div style={styles.container}><div style={styles.card}><p>Loading...</p></div></div>;

  if (recoveryKey) {
    return (
      <div style={styles.container}>
        <div style={styles.card}>
          <div style={styles.title}>Setup Complete</div>
          <div style={styles.recoveryBox}>
            <div style={styles.recoveryTitle}>Save Your Recovery Key</div>
            <p style={{ fontSize: '12px', color: '#aa8866' }}>
              This is shown ONCE. Save it somewhere safe. You'll need it if you forget your password.
            </p>
            <div style={styles.recoveryKey}>{recoveryKey}</div>
          </div>
          <button style={{ ...styles.button, marginTop: '24px' }} onClick={() => navigate('/')}>
            Continue to Dashboard
          </button>
        </div>
      </div>
    );
  }

  return (
    <div style={styles.container}>
      <div style={styles.card}>
        <div style={styles.title}>glyphTrader</div>
        <div style={styles.subtitle}>{isSetup ? 'First-Time Setup' : 'Login'}</div>

        {error && <div style={styles.error}>{error}</div>}

        <form onSubmit={isSetup ? handleSetup : handleLogin}>
          {isSetup && !showTokenField && (
            <div style={{ fontSize: '12px', color: '#666', marginBottom: '16px', textAlign: 'center' }}>
              Run <code style={{ color: '#00d4aa' }}>./setup.sh</code> for guided setup, or{' '}
              <span style={{ color: '#00d4aa', cursor: 'pointer', textDecoration: 'underline' }} onClick={() => setShowTokenField(true)}>
                enter setup token manually
              </span>
            </div>
          )}

          {isSetup && showTokenField && (
            <>
              <label style={styles.label}>Setup Token</label>
              <input style={styles.input} type="text" value={setupToken} onChange={(e) => setSetupToken(e.target.value)} placeholder="setup-xxxxxxxxxxxx" required />
            </>
          )}

          <label style={styles.label}>Password</label>
          <input style={styles.input} type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Enter password" required autoFocus />

          {isSetup && (
            <>
              <label style={styles.label}>Confirm Password</label>
              <input style={styles.input} type="password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} placeholder="Confirm password" required />
            </>
          )}

          <button type="submit" style={styles.button}>{isSetup ? 'Complete Setup' : 'Login'}</button>
        </form>
      </div>
    </div>
  );
}
