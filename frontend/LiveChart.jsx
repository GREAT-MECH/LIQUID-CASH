import React, { useEffect, useRef } from 'react';

const PAIR_TO_SYMBOL = {
  'EUR/USD': 'FX:EURUSD',
  'GBP/USD': 'FX:GBPUSD',
  'USD/JPY': 'FX:USDJPY',
  'GBP/JPY': 'FX:GBPJPY',
  'XAU/USD': 'OANDA:XAUUSD',
};

export default function LiveChart({ selectedPair = 'EUR/USD' }) {
  const containerRef = useRef(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    // Clear previous chart when switching pairs
    container.innerHTML = '';

    const script = document.createElement('script');
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js';
    script.type = 'text/javascript';
    script.async = true;
    script.innerHTML = JSON.stringify({
      autosize: true,
      symbol: PAIR_TO_SYMBOL[selectedPair] || 'FX:EURUSD',
      interval: '15',
      timezone: 'Etc/UTC',
      theme: 'dark',
      style: '1',
      locale: 'en',
      allow_symbol_change: false,
      hide_top_toolbar: false,
      save_image: false,
      support_host: 'https://www.tradingview.com',
    });

    container.appendChild(script);
  }, [selectedPair]);

  return (
    <div className="w-full h-[450px] rounded-xl overflow-hidden border border-slate-800">
      <div ref={containerRef} className="w-full h-full" />
    </div>
  );
}
