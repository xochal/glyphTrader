import React, { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate, Link, useNavigate } from 'react-router-dom';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import AutoTrades from './pages/AutoTrades';
import ManualTrades from './pages/ManualTrades';
import TradeHistory from './pages/TradeHistory';
import TradeSettings from './pages/TradeSettings';
import Settings from './pages/Settings';
import { DisclaimerModal, DisclaimerFooter } from './components/Disclaimer';
import PaperTradingBanner from './components/PaperTradingBanner';
import { isAuthenticated, logout, checkAuthStatus } from './services/auth';
import { getAccessToken } from './services/api';
import api from './services/api';

const styles = {
  app: { minHeight: '100vh', background: '#0f0f1a', color: '#e0e0e0', fontFamily: '-apple-system, BlinkMacSystemFont, monospace' } as React.CSSProperties,
  nav: { display: 'flex', alignItems: 'center', gap: '24px', padding: '12px 24px', background: '#1a1a2e', borderBottom: '1px solid #2a2a4a' } as React.CSSProperties,
  logo: { fontSize: '18px', fontWeight: 'bold', color: '#00d4aa', textDecoration: 'none' } as React.CSSProperties,
  link: { color: '#a0a0c0', textDecoration: 'none', fontSize: '14px', padding: '6px 12px', borderRadius: '4px' } as React.CSSProperties,
  activeLink: { color: '#00d4aa', background: '#2a2a4a' } as React.CSSProperties,
  logoutBtn: { marginLeft: 'auto', background: 'none', border: '1px solid #444', color: '#a0a0c0', padding: '6px 16px', borderRadius: '4px', cursor: 'pointer', fontSize: '13px' } as React.CSSProperties,
  content: { padding: '24px', maxWidth: '1400px', margin: '0 auto' } as React.CSSProperties,
};

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  if (!getAccessToken()) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function NavBar() {
  const navigate = useNavigate();
  const path = window.location.pathname;
  const [menuOpen, setMenuOpen] = useState(false);
  const [version, setVersion] = useState('');
  const [updateAvailable, setUpdateAvailable] = useState(false);

  useEffect(() => {
    if (!getAccessToken()) return;
    api.get('/health').then(r => setVersion(r.data.version)).catch(() => {});
    api.get('/settings/check-updates').then(r => {
      if (r.data.update_available) setUpdateAvailable(true);
    }).catch(() => {});
  }, []);

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  const closeMenu = () => setMenuOpen(false);
  const linkStyle = (p: string) => ({ ...styles.link, ...(path === p ? styles.activeLink : {}) });

  return (
    <nav style={styles.nav}>
      <span style={styles.logo}>glyphTrader</span>
      {version && <span style={{ fontSize: '11px', color: '#666' }}>v{version}</span>}
      {updateAvailable && (
        <Link to="/settings" style={{ fontSize: '11px', padding: '2px 8px', background: '#ffaa0022', color: '#ffaa00', borderRadius: '10px', border: '1px solid #ffaa0044', textDecoration: 'none' }}>
          Update Available
        </Link>
      )}
      <button className="hamburger" onClick={() => setMenuOpen(!menuOpen)}>
        {menuOpen ? '\u2715' : '\u2630'}
      </button>
      <div className={`nav-links${menuOpen ? ' open' : ''}`}>
        <Link to="/" style={linkStyle('/')} onClick={closeMenu}>Dashboard</Link>
        <Link to="/auto-trades" style={linkStyle('/auto-trades')} onClick={closeMenu}>Auto Trades</Link>
        <Link to="/manual-trades" style={linkStyle('/manual-trades')} onClick={closeMenu}>Manual Trades</Link>
        <Link to="/trades" style={linkStyle('/trades')} onClick={closeMenu}>Trade History</Link>
        <Link to="/trade-settings" style={linkStyle('/trade-settings')} onClick={closeMenu}>Trade Settings</Link>
        <Link to="/settings" style={linkStyle('/settings')} onClick={closeMenu}>Settings</Link>
        <a href="/docs/manual.html" target="_blank" rel="noopener noreferrer" style={styles.link} onClick={closeMenu}>User Guide</a>
        <button style={styles.logoutBtn} onClick={handleLogout}>Logout</button>
      </div>
    </nav>
  );
}

function AuthenticatedLayout({ children }: { children: React.ReactNode }) {
  const [disclaimerAcked, setDisclaimerAcked] = useState<boolean | null>(null);
  const [licenseValid, setLicenseValid] = useState(false);
  const [environment, setEnvironment] = useState('sandbox');

  useEffect(() => {
    if (!getAccessToken()) return;
    api.get('/trade-settings/disclaimer')
      .then(r => setDisclaimerAcked(r.data.acknowledged))
      .catch(() => setDisclaimerAcked(true)); // Don't block on error
    api.get('/settings/')
      .then(r => {
        setLicenseValid(r.data.license_valid || false);
        setEnvironment(r.data.tradier_environment || 'sandbox');
      })
      .catch(() => {});
  }, []);

  const handleAccept = async () => {
    try {
      await api.post('/trade-settings/disclaimer');
      setDisclaimerAcked(true);
    } catch {
      // Still dismiss on error
      setDisclaimerAcked(true);
    }
  };

  return (
    <>
      {disclaimerAcked === false && <DisclaimerModal onAccept={handleAccept} />}
      <PaperTradingBanner licenseValid={licenseValid} environment={environment} />
      <NavBar />
      <div className="content" style={styles.content}>
        {children}
        <DisclaimerFooter />
      </div>
    </>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <div style={styles.app}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<ProtectedRoute><AuthenticatedLayout><Dashboard /></AuthenticatedLayout></ProtectedRoute>} />
          <Route path="/auto-trades" element={<ProtectedRoute><AuthenticatedLayout><AutoTrades /></AuthenticatedLayout></ProtectedRoute>} />
          <Route path="/manual-trades" element={<ProtectedRoute><AuthenticatedLayout><ManualTrades /></AuthenticatedLayout></ProtectedRoute>} />
          <Route path="/trades" element={<ProtectedRoute><AuthenticatedLayout><TradeHistory /></AuthenticatedLayout></ProtectedRoute>} />
          <Route path="/trade-settings" element={<ProtectedRoute><AuthenticatedLayout><TradeSettings /></AuthenticatedLayout></ProtectedRoute>} />
          <Route path="/settings" element={<ProtectedRoute><AuthenticatedLayout><Settings /></AuthenticatedLayout></ProtectedRoute>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </BrowserRouter>
  );
}
