import React, { useEffect, useRef, useState, useCallback } from 'react';
import { createChart, ColorType } from 'lightweight-charts';
import {
  Activity,
  RefreshCw,
  ShieldAlert,
  Target,
  TrendingUp,
  TrendingDown,
  Zap,
  CheckCircle2,
  XCircle,
  Radio,
} from 'lucide-react';
import LiveChart from './LiveChart';

const API_BASE = import.meta.env.VITE_API_URL || '';
const PAIRS = ['EUR/USD', 'GBP/USD', 'USD/JPY', 'GBP/JPY', 'XAU/USD'];
const TIMEFRAMES = ['15min', '1h', '4h'];

export default function App() {
  const [selectedPair, setSelectedPair] = useState('EUR/USD');
  const [interval, setIntervalValue] = useState('1h');
  // Live TV Chart is the strict default so Twelve Data exhaustion never blocks viewing
  const [chartMode, setChartMode] = useState('tv');

  const [candles, setCandles] = useState([]);
  const [levels, setLevels] = useState(null);
  const [liveSignal, setLiveSignal] = useState(null);
  const [signals, setSignals] = useState([]);
  const [filterByPair, setFilterByPair] = useState(false);

  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [quotaNotice, setQuotaNotice] = useState('');
  const [lastUpdated, setLastUpdated] = useState('');

  const chartContainerRef = useRef(null);

  const fetchSignals = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/signals`);
      if (res.ok) {
        const data = await res.json();
        setSignals(Array.isArray(data) ? data : []);
      }
    } catch {
      // Keep existing signals if network blips
    }
  }, []);

  const fetchMarketMetrics = useCallback(async () => {
    setLoading(true);
    try {
      const encodedPair = encodeURIComponent(selectedPair);
      const res = await fetch(`${API_BASE}/api/chart/${encodedPair}?interval=${interval}`);
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        const detail = errBody.detail || '';
        if (detail.toLowerCase().includes('credit') || detail.toLowerCase().includes('limit') || res.status === 500) {
          setQuotaNotice('Twelve Data daily quota reached — Live TV Chart & Supabase signals remain active.');
        }
        return;
      }
      const data = await res.json();
      setCandles(data.candles || []);
      setLevels(data.levels || null);
      setLiveSignal(data.live_signal || null);
      setQuotaNotice(
        data.quota_paused
          ? 'Twelve Data rate-limit cooldown active (using cached liquidity levels).'
          : ''
      );
      setLastUpdated(new Date().toLocaleTimeString());
    } catch {
      setQuotaNotice('Offline / Engine waking up — Live TV Chart remains active.');
    } finally {
      setLoading(false);
    }
  }, [selectedPair, interval]);

  useEffect(() => {
    fetchMarketMetrics();
    fetchSignals();
    // Poll every 5 minutes (backend caches for 10 min, protecting 800/day Twelve Data cap)
    const timer = window.setInterval(() => {
      fetchMarketMetrics();
      fetchSignals();
    }, 300000);
    return () => window.clearInterval(timer);
  }, [fetchMarketMetrics, fetchSignals]);

  // Render Lightweight BSL/SSL Chart only when switched to 'bsl' mode
  useEffect(() => {
    if (chartMode !== 'bsl' || !chartContainerRef.current || candles.length === 0) return;

    const container = chartContainerRef.current;
    container.innerHTML = '';

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: '#0b1120' },
        textColor: '#94a3b8',
      },
      grid: {
        vertLines: { color: '#1e293b' },
        horzLines: { color: '#1e293b' },
      },
      width: container.clientWidth,
      height: 450,
      timeScale: { timeVisible: true, secondsVisible: false },
    });

    const series = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    });

    series.setData(candles);

    if (levels?.bsl) {
      series.createPriceLine({
        price: Number(levels.bsl),
        color: '#f59e0b',
        lineWidth: 2,
        lineStyle: 2,
        axisLabelVisible: true,
        title: 'BSL (Buy-Side Liquidity)',
      });
    }
    if (levels?.ssl) {
      series.createPriceLine({
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
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth });
      }
    };
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, [chartMode, candles, levels]);

  const handleManualScan = async () => {
    setScanning(true);
    try {
      await fetch(`${API_BASE}/api/scan`, { method: 'POST' });
      await Promise.all([fetchMarketMetrics(), fetchSignals()]);
    } finally {
      setScanning(false);
    }
  };

  const handleUpdateSignal = async (id, status) => {
    if (!id) return;
    try {
      await fetch(`${API_BASE}/api/signals/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      });
      fetchSignals();
    } catch {
      // Ignore transient failure
    }
  };

  const visibleSignals = filterByPair
    ? signals.filter((s) => s.pair === selectedPair)
    : signals;

  const closedTrades = signals.filter((s) => s.status === 'WON' || s.status === 'LOST');
  const wonTrades = signals.filter((s) => s.status === 'WON').length;
  const winRate = closedTrades.length > 0 ? Math.round((wonTrades / closedTrades.length) * 100) : null;

  return (
    <div className="min-h-screen bg-[#070d19] text-slate-100 p-4 md:p-6 font-sans">
      <div className="max-w-7xl mx-auto space-y-5">
        {/* Top Header */}
        <header className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 bg-[#0d1526] border border-slate-800/80 rounded-2xl p-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400">
              <Activity className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center gap-2.5">
                <h1 className="text-lg md:text-xl font-bold tracking-tight text-white">LIQUID CASH</h1>
                <span className="text-[11px] font-semibold px-2.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                  MAEE ENGINE
                </span>
              </div>
              <p className="text-xs text-slate-400">
                Institutional Liquidity Sweep &amp; Maximum Adverse/Favorable Excursion Monitor
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            {lastUpdated && (
              <span className="text-xs text-slate-400">Updated {lastUpdated}</span>
            )}
            <button
              onClick={handleManualScan}
              disabled={scanning}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white transition disabled:opacity-50"
            >
              <Radio className={`w-3.5 h-3.5 ${scanning ? 'animate-ping' : ''}`} />
              {scanning ? 'Scanning 5 Pairs...' : 'Scan All Pairs'}
            </button>
            <button
              onClick={() => {
                fetchMarketMetrics();
                fetchSignals();
              }}
              disabled={loading}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </button>
          </div>
        </header>

        {/* Graceful Twelve Data Quota Notice (Never blocks TradingView) */}
        {quotaNotice && (
          <div className="flex items-center justify-between gap-3 px-4 py-2.5 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-300 text-xs">
            <span>{quotaNotice}</span>
            <span className="font-semibold text-emerald-400 shrink-0">Live TV Chart Active</span>
          </div>
        )}

        {/* Pair Selector & Chart Mode Controls */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            {PAIRS.map((pair) => (
              <button
                key={pair}
                onClick={() => setSelectedPair(pair)}
                className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition border ${
                  selectedPair === pair
                    ? 'bg-emerald-500 text-slate-950 border-emerald-400 shadow-sm'
                    : 'bg-[#0d1526] text-slate-300 border-slate-800 hover:border-slate-700'
                }`}
              >
                {pair}
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center bg-[#0d1526] p-1 rounded-lg border border-slate-800">
              <button
                onClick={() => setChartMode('tv')}
                className={`px-3 py-1 rounded-md text-xs font-semibold transition ${
                  chartMode === 'tv'
                    ? 'bg-emerald-500 text-slate-950'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                Live TV Chart
              </button>
              <button
                onClick={() => setChartMode('bsl')}
                className={`px-3 py-1 rounded-md text-xs font-semibold transition ${
                  chartMode === 'bsl'
                    ? 'bg-amber-500 text-slate-950'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                BSL/SSL Chart
              </button>
            </div>

            <div className="flex items-center bg-[#0d1526] p-1 rounded-lg border border-slate-800">
              {TIMEFRAMES.map((tf) => (
                <button
                  key={tf}
                  onClick={() => setIntervalValue(tf)}
                  className={`px-2.5 py-1 rounded-md text-xs font-medium transition ${
                    interval === tf
                      ? 'bg-slate-700 text-white'
                      : 'text-slate-400 hover:text-white'
                  }`}
                >
                  {tf}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* 4 Live Institutional Metric Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="bg-[#0d1526] border border-slate-800/90 rounded-2xl p-4">
            <div className="flex items-center justify-between text-[11px] font-semibold tracking-wider text-slate-400 uppercase">
              <span>Buy-Side Liquidity (BSL)</span>
              <TrendingUp className="w-4 h-4 text-amber-400" />
            </div>
            <div className="mt-2 text-2xl font-bold text-amber-400">
              {levels?.bsl ?? '—'}
            </div>
            <p className="mt-1 text-xs text-slate-400">
              {levels?.dist_bsl_pips != null
                ? `${levels.dist_bsl_pips} pips above current price`
                : 'Equal highs / resting buy stops'}
            </p>
          </div>

          <div className="bg-[#0d1526] border border-slate-800/90 rounded-2xl p-4">
            <div className="flex items-center justify-between text-[11px] font-semibold tracking-wider text-slate-400 uppercase">
              <span>Sell-Side Liquidity (SSL)</span>
              <TrendingDown className="w-4 h-4 text-sky-400" />
            </div>
            <div className="mt-2 text-2xl font-bold text-sky-400">
              {levels?.ssl ?? '—'}
            </div>
            <p className="mt-1 text-xs text-slate-400">
              {levels?.dist_ssl_pips != null
                ? `${levels.dist_ssl_pips} pips below current price`
                : 'Equal lows / resting sell stops'}
            </p>
          </div>

          <div className="bg-[#0d1526] border border-slate-800/90 rounded-2xl p-4">
            <div className="flex items-center justify-between text-[11px] font-semibold tracking-wider text-slate-400 uppercase">
              <span>MAEE Excursion Ratio</span>
              <Zap className="w-4 h-4 text-purple-400" />
            </div>
            <div className="mt-2 text-2xl font-bold text-white">
              {levels?.maee_ratio != null ? `${levels.maee_ratio}R` : 'Active'}
            </div>
            <p className="mt-1 text-xs text-slate-400">
              {levels?.mfe_pips != null
                ? `+${levels.mfe_pips}p MFE vs -${levels.mae_pips}p MAE`
                : 'MFE vs MAE dynamic exhaustion threshold'}
            </p>
          </div>

          <div className="bg-[#0d1526] border border-slate-800/90 rounded-2xl p-4">
            <div className="flex items-center justify-between text-[11px] font-semibold tracking-wider text-slate-400 uppercase">
              <span>Exit Diagnostic</span>
              <ShieldAlert className="w-4 h-4 text-emerald-400" />
            </div>
            <div className="mt-2 text-lg font-bold text-emerald-400 truncate">
              {levels?.exit_state || 'MONITORING'}
            </div>
            <p className="mt-1 text-xs text-slate-400 truncate">
              {levels?.exit_detail || 'Monitoring momentum & sweep structure'}
            </p>
          </div>
        </div>

        {/* Active Setup Banner if triggered on current pair */}
        {liveSignal && (
          <div className="bg-emerald-500/10 border border-emerald-500/40 rounded-2xl p-4 flex flex-col md:flex-row md:items-center md:justify-between gap-3">
            <div className="flex items-start gap-3">
              <Target className="w-5 h-5 text-emerald-400 shrink-0 mt-0.5" />
              <div>
                <div className="text-sm font-bold text-emerald-300">
                  LIVE SETUP DETECTED: {liveSignal.pair} ({liveSignal.direction}) — {liveSignal.candle_pattern}
                </div>
                <div className="text-xs text-slate-300 mt-0.5">
                  Entry: <span className="font-mono text-white">{liveSignal.entry_price}</span> · SL:{' '}
                  <span className="font-mono text-rose-400">{liveSignal.stop_loss}</span> · Liquidity Exit TP:{' '}
                  <span className="font-mono text-amber-300">{liveSignal.take_profit_liquidity}</span> · 3x ATR Trail:{' '}
                  <span className="font-mono text-sky-300">{liveSignal.trailing_atr_exit}</span>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Main Chart Container (Live TV Chart by default) */}
        <div className="bg-[#0d1526] border border-slate-800/90 rounded-2xl p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-white">
              {selectedPair} {chartMode === 'tv' ? 'Live TradingView Chart' : `Liquidity & MAEE Chart (${interval})`}
            </h2>
            {levels && (
              <div className="flex items-center gap-4 text-xs">
                <span className="text-amber-400 font-medium">● BSL {levels.bsl}</span>
                <span className="text-sky-400 font-medium">● SSL {levels.ssl}</span>
              </div>
            )}
          </div>

          {chartMode === 'tv' ? (
            <LiveChart selectedPair={selectedPair} />
          ) : (
            <div ref={chartContainerRef} className="w-full h-[450px] rounded-xl overflow-hidden" />
          )}
        </div>

        {/* Supabase Signals & Outcome Tracking */}
        <div className="bg-[#0d1526] border border-slate-800/90 rounded-2xl p-4">
          <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
            <div className="flex items-center gap-2.5">
              <h3 className="text-sm font-semibold text-white">Recent Liquidity &amp; Exit Signals</h3>
              <span className="text-xs px-2 py-0.5 rounded-full bg-slate-800 text-slate-300">
                {visibleSignals.length} logged
              </span>
              {winRate !== null && (
                <span className="text-xs px-2.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 font-semibold">
                  Win Rate: {winRate}% ({wonTrades}/{closedTrades.length})
                </span>
              )}
            </div>

            <button
              onClick={() => setFilterByPair((prev) => !prev)}
              className={`text-xs px-3 py-1 rounded-lg border transition ${
                filterByPair
                  ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40'
                  : 'bg-slate-800/70 text-slate-300 border-slate-700 hover:text-white'
              }`}
            >
              {filterByPair ? `Showing ${selectedPair} Only` : 'Filter by Selected Pair'}
            </button>
          </div>

          {visibleSignals.length === 0 ? (
            <p className="text-xs text-slate-400 py-4 text-center">
              No signals logged yet. The backend scheduler scans every 15 minutes (or click{' '}
              <span className="text-emerald-400 font-medium">Scan All Pairs</span> above) for BSL/SSL sweeps and MAEE exit triggers.
            </p>
          ) : (
            <div className="space-y-2.5">
              {visibleSignals.map((sig, idx) => (
                <div
                  key={sig.id || idx}
                  className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 p-3 rounded-xl bg-[#070d19] border border-slate-800/80"
                >
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-xs font-bold text-white">{sig.pair}</span>
                      <span
                        className={`text-[11px] font-bold px-2 py-0.5 rounded ${
                          sig.direction === 'BUY'
                            ? 'bg-emerald-500/20 text-emerald-400'
                            : 'bg-rose-500/20 text-rose-400'
                        }`}
                      >
                        {sig.direction}
                      </span>
                      <span className="text-xs text-slate-300">{sig.candle_pattern}</span>
                      <span className="text-[11px] px-2 py-0.5 rounded bg-slate-800 text-slate-300">
                        {sig.status || 'ACTIVE'}
                      </span>
                    </div>
                    <div className="text-xs text-slate-400 mt-1">
                      Entry: <span className="text-slate-200 font-mono">{sig.entry_price}</span> · SL:{' '}
                      <span className="text-rose-400 font-mono">{sig.stop_loss}</span> · Exit TP:{' '}
                      <span className="text-amber-300 font-mono">{sig.take_profit_liquidity}</span> · 3x ATR Trail:{' '}
                      <span className="text-sky-300 font-mono">{sig.trailing_atr_exit}</span>
                    </div>
                  </div>

                  {sig.id && sig.status === 'ACTIVE' && (
                    <div className="flex items-center gap-2 shrink-0">
                      <button
                        onClick={() => handleUpdateSignal(sig.id, 'WON')}
                        className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-400 border border-emerald-500/30"
                      >
                        <CheckCircle2 className="w-3.5 h-3.5" />
                        Won
                      </button>
                      <button
                        onClick={() => handleUpdateSignal(sig.id, 'LOST')}
                        className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium bg-rose-500/15 hover:bg-rose-500/25 text-rose-400 border border-rose-500/30"
                      >
                        <XCircle className="w-3.5 h-3.5" />
                        Lost
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
