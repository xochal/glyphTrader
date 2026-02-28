import React from 'react';

const bannerStyle: React.CSSProperties = {
  background: '#2a2200',
  color: '#ffaa00',
  textAlign: 'center',
  padding: '8px 16px',
  fontSize: '13px',
  fontWeight: 'bold',
  borderBottom: '1px solid #554400',
  letterSpacing: '0.5px',
};

interface PaperTradingBannerProps {
  licenseValid: boolean;
  environment: string;
}

export default function PaperTradingBanner({ licenseValid, environment }: PaperTradingBannerProps) {
  if (licenseValid && environment === 'production') return null;

  const label = !licenseValid
    ? 'PAPER TRADING MODE — No production license'
    : 'PAPER TRADING MODE — Sandbox environment';

  return <div style={bannerStyle}>{label}</div>;
}
