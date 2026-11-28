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
  Copy,
  Check,
  Eye,
} from 'lucide-react';
import LiveChart from './LiveChart';

const API_BASE = import.meta.env.VITE_API_URL || '';
const PAIRS = ['EUR/USD', 'GBP/USD', 'USD/JPY', 'GBP/JPY', 'XAU/USD'];
const TIMEFRAMES = ['15min', '1h', '4h'];

export default function App() {
  const [selectedPair, setSelectedPair] = useState('EUR/USD');
  const [interval, setIntervalValue] = useState('1h');
  const [chartMode, setChartMode] = useState('tv');

  const [candles, setCandles] = useState([]);
  const [levels, setLevels] = useState(null);
  const [liveSignal, setLiveSignal] = useState(null);
  const [inspectedSignal, setInspectedSignal] = useState(null);
  const [signals, setSignals] = useState([]);
  const [filterByPair, setFilterByPair] = useState(false);

  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [quotaNotice, setQuotaNotice] = useState('');
  const [lastUpdated, setLastUpdated] = useState('');
  const [copiedId, setCopiedId] = useState(null);

  const chartContainerRef = useRef(null);

  const fetchSignals = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/signals`);
      if (res.ok) {
        const data = await res.json();
        setSignals(Array.isArray(data) ? data : []);
      }
    } catch {
      // Keep existing signals on transient network error
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
      if (data.live_signal) {
        setInspectedSignal(data.live_signal);
        setChartMode('bsl'); // Auto-display TradingView-style Position Overlay when a live setup fires
      }
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
    const timer = window.setInterval(() => {
      fetchMarketMetrics();
      fetchSignals();
    }, 300000);
    return () => window.clearInterval(timer);
  }, [fetchMarketMetrics, fetchSignals]);

  // Select the most recent logged signal for this pair if no live signal is currently pinned
  const activeOverlaySignal =
    inspectedSignal && inspectedSignal.pair === selectedPair
      ? inspectedSignal
      : liveSignal || signals.find((s) => s.pair === selectedPair) || null;

  // Render TradingView-Style Long/Short Position Tool + BSL/SSL Chart
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
      height: 460,
      timeScale: { timeVisible: true, secondsVisible: false },
      rightPriceScale: { borderColor: '#1e293b' },
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    });

    candleSeries.setData(candles);

    // Draw BSL & SSL Liquidity Lines
    if (levels?.bsl) {
      candleSeries.createPriceLine({
        price: Number(levels.bsl),
        color: '#f59e0b',
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: 'BSL',
      });
    }
    if (levels?.ssl) {
      candleSeries.createPriceLine({
        price: Number(levels.ssl),
        color: '#38bdf8',
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: 'SSL',
      });
    }

    // Draw TradingView-Style Long/Short Position Overlay (Shaded TP & SL Zones + 3x ATR Trail)
    if (activeOverlaySignal) {
      const entry = Number(activeOverlaySignal.entry_price);
      const sl = Number(activeOverlaySignal.stop_loss);
      const tp = Number(activeOverlaySignal.take_profit_liquidity);
      const trail = Number(activeOverlaySignal.trailing_atr_exit);
      const isBuy = activeOverlaySignal.direction === 'BUY';

      // Shade the right-most 25 candles like TradingView's Long/Short Position Tool
      const zoneCandles = candles.slice(-25);

      const tpZoneSeries = chart.addBaselineSeries({
        baseValue: { type: 'price', price: entry },
        topFillColor1: isBuy ? 'rgba(16, 185, 129, 0.26)' : 'rgba(239, 68, 68, 0.24)',
        topFillColor2: isBuy ? 'rgba(16, 185, 129, 0.08)' : 'rgba(239, 68, 68, 0.06)',
        bottomFillColor1: isBuy ? 'rgba(239, 68, 68, 0.06)' : 'rgba(16, 185, 129, 0.08)',
        bottomFillColor2: isBuy ? 'rgba(239, 68, 68, 0.24)' : 'rgba(16, 185, 129, 0.26)',
        topLineColor: 'transparent',
        bottomLineColor: 'transparent',
        priceLineVisible: false,
        lastValueVisible: false,
      });

      const slZoneSeries = chart.addBaselineSeries({
        baseValue: { type: 'price', price: entry },
        topFillColor1: isBuy ? 'rgba(16, 185, 129, 0.26)' : 'rgba(239, 68, 68, 0.24)',
        topFillColor2: isBuy ? 'rgba(16, 185, 129, 0.08)' : 'rgba(239, 68, 68, 0.06)',
        bottomFillColor1: isBuy ? 'rgba(239, 68, 68, 0.06)' : 'rgba(16, 185, 129, 0.08)',
        bottomFillColor2: isBuy ? 'rgba(239, 68, 68, 0.24)' : 'rgba(16, 185, 129, 0.26)',
        topLineColor: 'transparent',
        bottomLineColor: 'transparent',
        priceLineVisible: false,
        lastValueVisible: false,
      });

      if (!Number.isNaN(tp)) {
        tpZoneSeries.setData(zoneCandles.map((c) => ({ time: c.time, value: tp })));
      }
      if (!Number.isNaN(sl)) {
        slZoneSeries.setData(zoneCandles.map((c) => ({ time: c.time, value: sl })));
      }

      // Crisp Price Lines for Entry, Stop Loss, Exit TP, and 3x ATR Trail
      if (!Number.isNaN(tp)) {
        candleSeries.createPriceLine({
          price: tp,
          color: '#10b981',
          lineWidth: 2,
          lineStyle: 0,
          axisLabelVisible: true,
          title: `🎯 EXIT TP (${activeOverlaySignal.direction})`,
        });
      }
      if (!Number.isNaN(entry)) {
        candleSeries.createPriceLine({
          price: entry,
          color: '#e2e8f0',
          lineWidth: 2,
          lineStyle: 0,
          axisLabelVisible: true,
          title: `ENTRY (${activeOverlaySignal.direction})`,
        });
      }
      if (!Number.isNaN(trail)) {
        candleSeries.createPriceLine({
          price: trail,
          color: '#22d3ee',
          lineWidth: 2,
          lineStyle: 1,
          axisLabelVisible: true,
          title: '🛡️ 3x ATR TRAIL',
        });
      }
      if (!Number.isNaN(sl)) {
        candleSeries.createPriceLine({
          price: sl,
          color: '#f43f5e',
          lineWidth: 2,
          lineStyle: 0,
          axisLabelVisible: true,
          title: '🛑 STOP LOSS',
        });
      }
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
  }, [chartMode, candles, levels, activeOverlaySignal]);

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

  const handleCopyLevels = (sig, key) => {
    const text = [
      `LIQUID CASH — ${sig.pair} (${sig.direction})`,
      `Entry: ${sig.entry_price}`,
      `Stop Loss: ${sig.stop_loss}`,
      `Exit Target (TP): ${sig.take_profit_liquidity}`,
      `3x ATR Trail: ${sig.trailing_atr_exit}`,
    ].join('\n');
    navigator.clipboard.writeText(text);
    setCopiedId(key);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleVisualizeSignal = (sig) => {
    setSelectedPair(sig.pair);
    setInspectedSignal(sig);
    setChartMode('bsl');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const visibleSignals = filterByPair
    ? signals.filter((s) => s.pair === selectedPair)
    : signals;

  const closedTrades = signals.filter((s) => s.status === 'WON' || s.status === 'LOST');
  const wonTrades = signals.filter((s) => s.status === 'WON').length;
  const winRate = closedTrades.length > 0 ? Math.round((wonTrades / closedTrades.length) * 100) : null;

  // Calculate Risk:Reward ratio for the active overlay signal
  const calcRR = (sig) => {
    if (!sig) return null;
    const risk = Math.abs(Number(sig.entry_price) - Number(sig.stop_loss));
    const reward = Math.abs(Number(sig.take_profit_liquidity) - Number(sig.entry_price));
    if (!risk) return null;
    return (reward / risk).toFixed(2);
  };

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
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-lg md:text-xl font-bold tracking-tight text-white">LIQUID CASH</h1>
                <span className="text-[11px] font-semibold px-2.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                  MAEE ENGINE
                </span>
                {levels?.session && (
                  <span
                    className={`text-[11px] font-medium px-2.5 py-0.5 rounded-full border ${
                      levels.in_session
                        ? 'bg-sky-500/15 text-sky-300 border-sky-500/30'
                        : 'bg-slate-800 text-slate-400 border-slate-700'
                    }`}
                  >
                    {levels.session}
                  </span>
                )}
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

        {/* Graceful Twelve Data Quota Notice */}
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
                Trade Setup &amp; BSL/SSL
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

        {/* Active Setup Banner with Copy-Trade Levels */}
        {activeOverlaySignal && (
          <div className="bg-emerald-500/10 border border-emerald-500/40 rounded-2xl p-4 flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3">
            <div className="flex items-start gap-3">
              <Target className="w-5 h-5 text-emerald-400 shrink-0 mt-0.5" />
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-bold text-emerald-300">
                    {activeOverlaySignal.pair} ({activeOverlaySignal.direction}) — {activeOverlaySignal.candle_pattern}
                  </span>
                  {calcRR(activeOverlaySignal) && (
                    <span className="text-[11px] font-bold px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300">
                      {calcRR(activeOverlaySignal)}R Target
                    </span>
                  )}
                </div>
                <div className="text-xs text-slate-300 mt-1 flex flex-wrap gap-x-3 gap-y-1">
                  <span>
                    Entry: <strong className="font-mono text-white">{activeOverlaySignal.entry_price}</strong>
                  </span>
                  <span>
                    Stop Loss: <strong className="font-mono text-rose-400">{activeOverlaySignal.stop_loss}</strong>
                  </span>
                  <span>
                    Exit TP: <strong className="font-mono text-emerald-400">{activeOverlaySignal.take_profit_liquidity}</strong>
                  </span>
                  <span>
                    3x ATR Trail: <strong className="font-mono text-cyan-300">{activeOverlaySignal.trailing_atr_exit}</strong>
                  </span>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2 shrink-0">
              {chartMode !== 'bsl' && (
                <button
                  onClick={() => setChartMode('bsl')}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-amber-500 text-slate-950 hover:bg-amber-400 transition"
                >
                  <Eye className="w-3.5 h-3.5" />
                  Show TP/SL on Chart
                </button>
              )}
              <button
                onClick={() => handleCopyLevels(activeOverlaySignal, 'banner')}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-white border border-slate-700 transition"
              >
                {copiedId === 'banner' ? (
                  <>
                    <Check className="w-3.5 h-3.5 text-emerald-400" />
                    Copied!
                  </>
                ) : (
                  <>
                    <Copy className="w-3.5 h-3.5" />
                    Copy MT4/MT5 Levels
                  </>
                )}
              </button>
            </div>
          </div>
        )}

        {/* Main Chart Container */}
        <div className="bg-[#0d1526] border border-slate-800/90 rounded-2xl p-4">
          <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
            <h2 className="text-sm font-semibold text-white">
              {selectedPair}{' '}
              {chartMode === 'tv'
                ? 'Live TradingView Chart'
                : `Trade Position & Liquidity Chart (${interval})`}
            </h2>
            <div className="flex flex-wrap items-center gap-3 text-xs">
              {activeOverlaySignal && chartMode === 'bsl' && (
                <>
                  <span className="text-emerald-400 font-medium">■ TP {activeOverlaySignal.take_profit_liquidity}</span>
                  <span className="text-slate-200 font-medium">― Entry {activeOverlaySignal.entry_price}</span>
                  <span className="text-cyan-300 font-medium">┄ 3x ATR {activeOverlaySignal.trailing_atr_exit}</span>
                  <span className="text-rose-400 font-medium">■ SL {activeOverlaySignal.stop_loss}</span>
                </>
              )}
              {levels && (
                <>
                  <span className="text-amber-400 font-medium">● BSL {levels.bsl}</span>
                  <span className="text-sky-400 font-medium">● SSL {levels.ssl}</span>
                </>
              )}
            </div>
          </div>

          {chartMode === 'tv' ? (
            <LiveChart selectedPair={selectedPair} />
          ) : (
            <div ref={chartContainerRef} className="w-full h-[460px] rounded-xl overflow-hidden" />
          )}
        </div>

        {/* Supabase Signals & Copy-Trade Log */}
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
              No signals logged yet. The backend scheduler scans every 15 minutes during London &amp; NY sessions (or click{' '}
              <span className="text-emerald-400 font-medium">Scan All Pairs</span> above) for BSL/SSL sweeps and MAEE exit triggers.
            </p>
          ) : (
            <div className="space-y-2.5">
              {visibleSignals.map((sig, idx) => {
                const sigKey = sig.id || idx;
                return (
                  <div
                    key={sigKey}
                    className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3 p-3 rounded-xl bg-[#070d19] border border-slate-800/80"
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
                        <span className="text-emerald-400 font-mono">{sig.take_profit_liquidity}</span> · 3x ATR Trail:{' '}
                        <span className="text-cyan-300 font-mono">{sig.trailing_atr_exit}</span>
                      </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-2 shrink-0">
                      <button
                        onClick={() => handleVisualizeSignal(sig)}
                        className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium bg-amber-500/15 hover:bg-amber-500/25 text-amber-300 border border-amber-500/30"
                      >
                        <Eye className="w-3.5 h-3.5" />
                        Plot on Chart
                      </button>

                      <button
                        onClick={() => handleCopyLevels(sig, sigKey)}
                        className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700"
                      >
                        {copiedId === sigKey ? (
                          <>
                            <Check className="w-3.5 h-3.5 text-emerald-400" />
                            Copied
                          </>
                        ) : (
                          <>
                            <Copy className="w-3.5 h-3.5" />
                            Copy Levels
                          </>
                        )}
                      </button>

                      {sig.id && sig.status === 'ACTIVE' && (
                        <>
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
                        </>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
