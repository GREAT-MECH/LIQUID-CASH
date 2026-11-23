import React, { useEffect, useRef, useState } from 'react';
import { createChart } from 'lightweight-charts';
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  CheckCircle2,
  Clock,
  Crosshair,
  Layers,
  RefreshCw,
  ShieldAlert,
  TrendingDown,
  TrendingUp,
} from 'lucide-react';

const PAIRS = ['EUR/USD', 'GBP/USD', 'USD/JPY', 'XAU/USD'];
const TIMEFRAMES = ['15min', '1h', '4h'];

export default function App() {
  const [selectedPair, setSelectedPair] = useState('EUR/USD');
  const [timeframe, setTimeframe] = useState('1h');
  const [chartData, setChartData] = useState(null);
  const [signals, setSignals] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);

  const chartContainerRef = useRef(null);
  const chartInstanceRef = useRef(null);

  const apiBase = (
    import.meta.env.VITE_API_URL || 'https://liquid-cash-api.onrender.com'
  ).replace(/\/$/, '');

  const fetchDashboardData = async () => {
    setLoading(true);
    setError(null);
    try {
      const encodedPair = encodeURIComponent(selectedPair);
      let chartRes = await fetch(
        `${apiBase}/api/chart/${selectedPair}?interval=${timeframe}`
      );

      if (chartRes.status === 404) {
        chartRes = await fetch(
          `${apiBase}/api/chart/${encodedPair}?interval=${timeframe}`
        );
      }

      const signalsRes = await fetch(`${apiBase}/api/signals`).catch(() => null);

      if (!chartRes.ok) {
        const errBody = await chartRes.json().catch(() => ({}));
        throw new Error(errBody.detail || `Chart API error (${chartRes.status})`);
      }

      const chartJson = await chartRes.json();
      setChartData(chartJson);

      if (signalsRes && signalsRes.ok) {
        const signalsJson = await signalsRes.json();
        setSignals(Array.isArray(signalsJson) ? signalsJson : []);
      }

      setLastUpdated(new Date().toLocaleTimeString());
    } catch (err) {
      setError(err.message || 'Failed to connect to LIQUID-CASH backend');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDashboardData();
    const timer = setInterval(fetchDashboardData, 60000);
    return () => clearInterval(timer);
  }, [selectedPair, timeframe]);

  useEffect(() => {
    if (!chartContainerRef.current || !chartData?.candles?.length) return;

    if (chartInstanceRef.current) {
      chartInstanceRef.current.remove();
      chartInstanceRef.current = null;
    }

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { color: '#0b101b' },
        textColor: '#94a3b8',
      },
      grid: {
        vertLines: { color: '#1e293b' },
        horzLines: { color: '#1e293b' },
      },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: '#1e293b' },
      timeScale: {
        borderColor: '#1e293b',
        timeVisible: true,
        secondsVisible: false,
      },
      width: chartContainerRef.current.clientWidth,
      height: 440,
    });

    chartInstanceRef.current = chart;

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    });

    const formattedCandles = chartData.candles
      .map((c) => ({
        time:
          typeof c.time === 'number'
            ? c.time
            : Math.floor(new Date(c.time).getTime() / 1000),
        open: Number(c.open),
        high: Number(c.high),
        low: Number(c.low),
        close: Number(c.close),
      }))
      .sort((a, b) => a.time - b.time);

    candleSeries.setData(formattedCandles);

    const levels = chartData.levels || {};
    if (levels.bsl) {
      candleSeries.createPriceLine({
        price: Number(levels.bsl),
        color: '#f59e0b',
        lineWidth: 2,
        lineStyle: 2,
        axisLabelVisible: true,
        title: 'BSL (Buy-Side Liquidity)',
      });
    }
    if (levels.ssl) {
      candleSeries.createPriceLine({
        price: Number(levels.ssl),
        color: '#38bdf8',
        lineWidth: 2,
        lineStyle: 2,
        axisLabelVisible: true,
        title: 'SSL (Sell-Side Liquidity)',
      });
    }

    chart.timeScale().fitContent();

    const handleResize = () => {
      if (chartContainerRef.current && chartInstanceRef.current) {
        chartInstanceRef.current.applyOptions({
          width: chartContainerRef.current.clientWidth,
        });
      }
    };

    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      if (chartInstanceRef.current) {
        chartInstanceRef.current.remove();
        chartInstanceRef.current = null;
      }
    };
  }, [chartData]);

  const levels = chartData?.levels || {};
  const liveSig = chartData?.live_signal || {};
  const exitStatus = liveSig.signal_type || liveSig.status || 'MONITORING';
  const isExitTriggered =
    String(exitStatus).toUpperCase().includes('EXIT') ||
    String(exitStatus).toUpperCase().includes('CLOSE');

  return (
    <div className="min-h-screen bg-[#060911] text-slate-100 font-sans">
      <header className="border-b border-slate-800/80 bg-[#0b101b]/90 backdrop-blur sticky top-0 z-20">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-4 flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 rounded-xl bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center">
              <Activity className="h-5 w-5 text-emerald-400" />
            </div>
            <div>
              <h1 className="text-lg font-bold tracking-wide text-white flex items-center gap-2">
                LIQUID CASH
                <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                  MAEE ENGINE
                </span>
              </h1>
              <p className="text-xs text-slate-400">
                Institutional Liquidity Sweep &amp; Maximum Adverse/Favorable Excursion Monitor
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {lastUpdated && (
              <span className="text-xs text-slate-400 flex items-center gap-1.5">
                <Clock className="h-3.5 w-3.5 text-slate-500" />
                Updated {lastUpdated}
              </span>
            )}
            <button
              onClick={fetchDashboardData}
              disabled={loading}
              className="flex items-center gap-2 px-3.5 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-xs font-medium text-slate-200 transition disabled:opacity-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 py-6 space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-4 bg-[#0b101b] p-3.5 rounded-xl border border-slate-800/80">
          <div className="flex flex-wrap items-center gap-2">
            {PAIRS.map((pair) => (
              <button
                key={pair}
                onClick={() => setSelectedPair(pair)}
                className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition ${
                  selectedPair === pair
                    ? 'bg-emerald-500 text-slate-950 shadow-lg shadow-emerald-500/20'
                    : 'bg-slate-900 text-slate-300 hover:bg-slate-800 border border-slate-800'
                }`}
              >
                {pair}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-1.5 bg-slate-900 p-1 rounded-lg border border-slate-800">
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf}
                onClick={() => setTimeframe(tf)}
                className={`px-3 py-1 rounded-md text-xs font-medium transition ${
                  timeframe === tf
                    ? 'bg-slate-700 text-white'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {tf}
              </button>
            ))}
          </div>
        </div>

        {error && (
          <div className="p-4 rounded-xl bg-red-500/10 border border-red-500/30 flex items-center gap-3 text-red-300 text-sm">
            <AlertTriangle className="h-5 w-5 text-red-400 shrink-0" />
            <div>
              <p className="font-semibold">Backend Connection Notice</p>
              <p className="text-xs text-red-300/80 mt-0.5">{error}</p>
            </div>
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="bg-[#0b101b] p-4 rounded-xl border border-slate-800/80">
            <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
              <span>BUY-SIDE LIQUIDITY (BSL)</span>
              <ArrowUpRight className="h-4 w-4 text-amber-400" />
            </div>
            <div className="text-xl font-bold text-amber-400 font-mono">
              {levels.bsl ? Number(levels.bsl).toFixed(5) : 'Scanning...'}
            </div>
            <p className="text-[11px] text-slate-500 mt-1">Equal highs / resting buy stops</p>
          </div>

          <div className="bg-[#0b101b] p-4 rounded-xl border border-slate-800/80">
            <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
              <span>SELL-SIDE LIQUIDITY (SSL)</span>
              <ArrowDownRight className="h-4 w-4 text-sky-400" />
            </div>
            <div className="text-xl font-bold text-sky-400 font-mono">
              {levels.ssl ? Number(levels.ssl).toFixed(5) : 'Scanning...'}
            </div>
            <p className="text-[11px] text-slate-500 mt-1">Equal lows / resting sell stops</p>
          </div>

          <div className="bg-[#0b101b] p-4 rounded-xl border border-slate-800/80">
            <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
              <span>MAEE EXCURSION RATIO</span>
              <Crosshair className="h-4 w-4 text-purple-400" />
            </div>
            <div className="text-xl font-bold text-white font-mono">
              {levels.maee_ratio !== undefined
                ? `${Number(levels.maee_ratio).toFixed(2)}x`
                : 'Active'}
            </div>
            <p className="text-[11px] text-slate-500 mt-1">
              MFE vs MAE dynamic exhaustion threshold
            </p>
          </div>

          <div
            className={`p-4 rounded-xl border ${
              isExitTriggered
                ? 'bg-red-500/10 border-red-500/40'
                : 'bg-[#0b101b] border-slate-800/80'
            }`}
          >
            <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
              <span>EXIT DIAGNOSTIC</span>
              {isExitTriggered ? (
                <ShieldAlert className="h-4 w-4 text-red-400" />
              ) : (
                <CheckCircle2 className="h-4 w-4 text-emerald-400" />
              )}
            </div>
            <div
              className={`text-xl font-bold tracking-tight ${
                isExitTriggered ? 'text-red-400' : 'text-emerald-400'
              }`}
            >
              {exitStatus}
            </div>
            <p className="text-[11px] text-slate-400 mt-1">
              {liveSig.notes || 'Monitoring momentum & sweep structure'}
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 bg-[#0b101b] rounded-xl border border-slate-800/80 p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Layers className="h-4 w-4 text-emerald-400" />
                <h2 className="text-sm font-semibold text-white">
                  {selectedPair} Live Liquidity &amp; MAEE Chart ({timeframe})
                </h2>
              </div>
              <div className="flex items-center gap-3 text-[11px] text-slate-400">
                <span className="flex items-center gap-1">
                  <span className="h-2 w-2 rounded-full bg-amber-400 inline-block" /> BSL
                </span>
                <span className="flex items-center gap-1">
                  <span className="h-2 w-2 rounded-full bg-sky-400 inline-block" /> SSL
                </span>
              </div>
            </div>

            <div className="relative w-full h-[440px]">
              {loading && !chartData && (
                <div className="absolute inset-0 flex items-center justify-center text-sm text-slate-400 z-10">
                  Loading market candles &amp; liquidity pools...
                </div>
              )}
              <div ref={chartContainerRef} className="w-full h-[440px]" />
            </div>
          </div>

          <div className="bg-[#0b101b] rounded-xl border border-slate-800/80 p-4 flex flex-col">
            <h2 className="text-sm font-semibold text-white mb-3 flex items-center justify-between">
              <span>Recent Liquidity &amp; Exit Signals</span>
              <span className="text-xs font-normal text-slate-400">
                {signals.length} logged
              </span>
            </h2>

            <div className="space-y-3 overflow-y-auto max-h-[440px] pr-1">
              {signals.length === 0 ? (
                <div className="text-xs text-slate-500 bg-slate-900/60 border border-slate-800/80 rounded-lg p-4 text-center">
                  No signals logged in Supabase yet. The backend scheduler scans every minute for BSL/SSL sweeps and MAEE exit triggers.
                </div>
              ) : (
                signals.map((sig, idx) => {
                  const isBuy = String(sig.signal_type || '')
                    .toUpperCase()
                    .includes('BUY');
                  return (
                    <div
                      key={sig.id || idx}
                      className="p-3 rounded-lg bg-slate-900/80 border border-slate-800 hover:border-slate-700 transition"
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-bold text-white flex items-center gap-1.5">
                          {isBuy ? (
                            <TrendingUp className="h-3.5 w-3.5 text-emerald-400" />
                          ) : (
                            <TrendingDown className="h-3.5 w-3.5 text-red-400" />
                          )}
                          {sig.pair || selectedPair}
                        </span>
                        <span
                          className={`text-[10px] font-bold px-2 py-0.5 rounded ${
                            isBuy
                              ? 'bg-emerald-500/15 text-emerald-400'
                              : 'bg-red-500/15 text-red-400'
                          }`}
                        >
                          {sig.signal_type || 'SIGNAL'}
                        </span>
                      </div>

                      <div className="mt-2 grid grid-cols-2 gap-2 text-[11px] text-slate-400">
                        <div>
                          Entry:{' '}
                          <span className="text-slate-200 font-mono">
                            {sig.entry_price || '—'}
                          </span>
                        </div>
                        <div>
                          Exit/TP:{' '}
                          <span className="text-slate-200 font-mono">
                            {sig.exit_price || '—'}
                          </span>
                        </div>
                      </div>

                      {sig.notes && (
                        <p className="mt-1.5 text-[11px] text-slate-400 line-clamp-2">
                          {sig.notes}
                        </p>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
