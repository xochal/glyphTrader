import React from 'react';

const modalOverlay: React.CSSProperties = {
  position: 'fixed', top: 0, left: 0, width: '100vw', height: '100vh',
  background: 'rgba(0,0,0,0.85)', display: 'flex', alignItems: 'center',
  justifyContent: 'center', zIndex: 9999,
};

const modalBox: React.CSSProperties = {
  background: '#1a1a2e', border: '1px solid #2a2a4a', borderRadius: '12px',
  padding: '32px', maxWidth: '640px', width: '90%', maxHeight: '85vh',
  overflowY: 'auto', color: '#e0e0e0',
};

const title: React.CSSProperties = {
  fontSize: '18px', fontWeight: 'bold', color: '#ff6644', marginBottom: '20px',
  textAlign: 'center',
};

const bulletList: React.CSSProperties = {
  paddingLeft: '20px', margin: '16px 0', lineHeight: '1.8', fontSize: '13px',
};

const acceptBtn: React.CSSProperties = {
  display: 'block', width: '100%', padding: '12px', marginTop: '24px',
  background: '#00d4aa', color: '#0f0f1a', border: 'none', borderRadius: '8px',
  fontSize: '14px', fontWeight: 'bold', cursor: 'pointer',
};

const footerBar: React.CSSProperties = {
  textAlign: 'center', padding: '8px', fontSize: '11px', color: '#666',
  borderTop: '1px solid #1a1a2e', marginTop: '24px',
};

export function DisclaimerModal({ onAccept }: { onAccept: () => void }) {
  return (
    <div style={modalOverlay}>
      <div style={modalBox}>
        <div style={title}>IMPORTANT DISCLAIMER</div>
        <p style={{ fontSize: '13px', lineHeight: '1.6' }}>
          This software is provided "as-is" without warranty of any kind, express or implied.
          It is a personal trading tool, not investment advice.
        </p>
        <ul style={bulletList}>
          <li>The developer is not a registered investment adviser, broker-dealer, or financial planner.</li>
          <li>This software does not consider your individual financial situation, risk tolerance, or investment objectives.</li>
          <li>Trading stocks involves substantial risk of loss. You may lose some or all of your invested capital.</li>
          <li>Past performance of any strategy, including those implemented in this software, does not guarantee future results.</li>
          <li>You are solely responsible for all trading decisions made using this software and their financial outcomes.</li>
          <li>No guarantee is made regarding the accuracy, reliability, or completeness of the software's analysis or signals.</li>
        </ul>
        <p style={{ fontSize: '13px', lineHeight: '1.6', color: '#a0a0c0' }}>
          By clicking "I Understand and Accept," you acknowledge that you have read this disclaimer,
          that you are using this software at your own risk, and that you will not hold the developer
          liable for any financial losses.
        </p>
        <button style={acceptBtn} onClick={onAccept}>I Understand and Accept</button>
      </div>
    </div>
  );
}

export function DisclaimerFooter() {
  return (
    <div style={footerBar}>
      Not investment advice. Trading involves risk of loss. Use at your own risk.
    </div>
  );
}
