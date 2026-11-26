import React, { useCallback, useEffect, useState } from 'react';
import { ArrowUpRight, BarChart3, RefreshCw, Radar, Waves } from 'lucide-react';
import axios from 'axios';
import { Link } from 'react-router-dom';

const display = (value) => value === null || value === undefined || value === '' ? '—' : String(value);
const formatTime = (value) => {
  if (!value) return 'Awaiting snapshot';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString([], { dateStyle: 'short', timeStyle: 'short' });
};

export default function MarketPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lastRefreshAt, setLastRefreshAt] = useState(null);
  const load = useCallback(() => {
    setError('');
    setLoading(true);
    axios.get('/api/admin/overview').then((response) => { setData(response.data); setLastRefreshAt(new Date()); }).catch(() => setError('Market intelligence is unavailable.')).finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(); const interval = setInterval(load, 15000); return () => clearInterval(interval); }, [load]);

  const market = data?.market || {};
  const rows = Object.values(market.heatmap || {});
  const correlations = market.correlation?.pairs || [];

  return (
    <div className="page-shell">
      <div className="page-heading"><div><div className="eyebrow accent">MARKET STATE / 02</div><h1 className="page-title">Market Intelligence</h1><p className="page-subtitle">The same structured market context exposed through ATLAX Telegram, organized for operator review.</p></div><div className="page-actions"><span className="data-as-of">{loading ? 'Refreshing…' : `Synced ${formatTime(lastRefreshAt || data?.generated_at)}`}</span><button className="btn btn-icon-label" onClick={load} disabled={loading}><RefreshCw size={15} className={loading ? 'pulse-anim' : ''} /> {loading ? 'Refreshing' : 'Refresh'}</button></div></div>
      {error && <div className="notice notice-danger">{error}</div>}
      <div className="system-strip"><div className="status-chip"><span className="status-icon accent-icon"><Radar size={15} /></span><span><small>RADAR EVENTS</small><strong>{rows.length} tracked</strong></span></div><div className="status-chip"><span className="status-icon"><Waves size={15} /></span><span><small>CORRELATION PAIRS</small><strong>{correlations.length} computed</strong></span></div><div className="status-chip"><span className="status-icon"><BarChart3 size={15} /></span><span><small>DATA AS OF</small><strong>{formatTime(data?.generated_at)}</strong></span></div></div>
      <section className="dashboard-grid market-grid">
        <div className="data-panel table-panel"><div className="panel-heading"><div><span className="eyebrow">HEATMAP</span><h2>Active market radar</h2></div><span className="panel-caption">Live runtime state</span></div><div className="table-scroll"><table className="premium-table dense-table"><thead><tr><th>Instrument</th><th>Bias</th><th>Distance</th><th>Parent range</th><th>State</th></tr></thead><tbody>{rows.length === 0 ? <tr><td colSpan="5" className="empty-cell">No market events available.</td></tr> : rows.map((row) => { const bullish = String(row.bias || '').toLowerCase().includes('bull'); return <tr key={`${row.symbol}-${row.timeframe}`}><td><strong>{display(row.symbol)}</strong><span className="cell-sub">{display(row.timeframe)}</span></td><td><span className={bullish ? 'direction bullish' : 'direction bearish'}>{bullish ? '↑' : '↓'} {bullish ? 'Bullish' : 'Bearish'}</span></td><td>{display(row.distance_pips)} <span className="cell-sub">pips</span></td><td className="cell-sub">{display(row.parent_low)} — {display(row.parent_high)}</td><td><span className={`badge ${row.status === 'Sweeping Now' ? 'badge-warning' : 'badge-outline'}`}>{display(row.status)}</span></td></tr>; })}</tbody></table></div></div>
        <div className="data-panel"><div className="panel-heading"><div><span className="eyebrow">CORRELATION</span><h2>Pair relationships</h2></div><Link className="icon-link" to="/" title="Back to command center"><ArrowUpRight size={16} /></Link></div><div className="correlation-list">{correlations.length === 0 ? <div className="empty-cell">Correlation state is not ready yet.</div> : correlations.slice(0, 10).map((pair) => <div className="correlation-row" key={`${pair.pair_a}-${pair.pair_b}`}><div><strong>{pair.pair_a} / {pair.pair_b}</strong><span>{pair.label}</span></div><strong className={Number(pair.coefficient) < 0 ? 'negative-correlation' : 'positive-correlation'}>{Number(pair.coefficient).toFixed(3)}</strong></div>)}</div><p className="panel-note">{display(market.correlation?.note || 'Correlation uses the persisted ATLAX daily-close state and does not guarantee future co-movement.')}</p></div>
      </section>
    </div>
  );
}
