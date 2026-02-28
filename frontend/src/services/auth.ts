import api, { setAccessToken, getAccessToken } from './api';

let idleTimer: ReturnType<typeof setTimeout> | null = null;
const IDLE_TIMEOUT = 10 * 60 * 1000; // 10 minutes

export async function login(password: string): Promise<string> {
  const resp = await api.post('/auth/login', { password });
  const token = resp.data.access_token;
  setAccessToken(token);
  resetIdleTimer();
  return token;
}

export async function setup(setupToken: string, password: string): Promise<any> {
  const resp = await api.post('/auth/setup', { setup_token: setupToken, password });
  const data = resp.data;
  setAccessToken(data.access_token);
  resetIdleTimer();
  return data;
}

export async function logout(): Promise<void> {
  try {
    await api.post('/auth/logout');
  } catch {}
  setAccessToken(null);
  clearIdleTimer();
}

export function isAuthenticated(): boolean {
  return getAccessToken() !== null;
}

export async function checkAuthStatus(): Promise<{ setup_complete: boolean; locked: boolean }> {
  const resp = await api.get('/auth/status');
  return resp.data;
}

function resetIdleTimer() {
  clearIdleTimer();
  idleTimer = setTimeout(() => {
    setAccessToken(null);
    window.location.href = '/login';
  }, IDLE_TIMEOUT);
}

function clearIdleTimer() {
  if (idleTimer) {
    clearTimeout(idleTimer);
    idleTimer = null;
  }
}

// Reset idle timer on user activity
if (typeof window !== 'undefined') {
  ['mousedown', 'keydown', 'scroll', 'touchstart'].forEach((event) => {
    window.addEventListener(event, () => {
      if (getAccessToken()) resetIdleTimer();
    });
  });
}
