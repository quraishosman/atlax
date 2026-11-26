import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  BarChart3,
  CheckCircle2,
  Clock3,
  Crosshair,
  Gauge,
  RefreshCw,
  ShieldCheck,
  Signal,
  Users,
  WalletCards,
  Zap,
} from 'lucide-react';
import axios from 'axios';

const formatNumber = (value, digits = 0) => {
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString(undefined, { maximumFractionDigits: digits }) : '—';
};

const formatMoney = (value) => {
  const number = Number(value);
  return Number.isFinite(number) ? `$${number.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—';
};

const formatTime = (value) => {
  if (!value) return 'Awaiting snapshot';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString([], { dateStyle: 'short', timeStyle: 'short' });
};

const stateClass = (value) => {
  const status = String(value || '').toUpperCase();
  if (['HEALTHY', 'CONNECTED', 'ACTIVE', 'ENABLED', 'NORMAL', 'AUTO-ENFORCED', 'FILLED', 'CONFIRMED'].includes(status)) return 'badge-success';
  if (['BLOCKED', 'DEGRADED', 'UNKNOWN', 'UNAVAILABLE', 'DISABLED', 'FAILED', 'REJECTED'].includes(status)) return 'badge-danger';
  return 'badge-outline';
};

const display = (value) => value === null || value === undefined || value === '' ? '—' : String(value);

function Metric({ label, value, note, icon: Icon, tone = '' }) {
  return (
    <div className="metric-card compact-metric">
      <div className="metric-header"><span className="metric-label">{label}</span><Icon size={16} /></div>
      <div className={`metric-value ${tone}`}>{value}</div>
      <span className="metric-note">{note}</span>
    </div>
  );
}

export default function DashboardPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lastRefreshAt, setLastRefreshAt] = useState(null);

  const fetchOverview = useCallback(() => {
    setError('');
    axios.get('/api/admin/overview')
      .then((response) => { setData(response.data); setLastRefreshAt(new Date()); })
      .catch(() => setError('Live overview is unavailable. Check the API and scanner services.'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    fetchOverview();
    const interval = setInterval(fetchOverview, 15000);
    return () => clearInterval(interval);
  }, [fetchOverview]);

  const metrics = data?.metrics || {};
  const operations = data?.operations || {};
  const market = data?.market || {};
  const heatmap = Object.values(market.heatmap || {});
  const billing = data?.billing || {};
  const access = data?.access || {};
  const propFirm = data?.prop_firm || {};
  const watchdog = operations.watchdog || {};
  const breaker = operations.circuit_breaker || {};
  const mode = operations.mode || {};
  const radarRows = useMemo(() => [...heatmap].sort((a, b) => Math.abs(Number(a.distance_pips || 0)) - Math.abs(Number(b.distance_pips || 0))).slice(0, 8), [heatmap]);
  const qualityRows = operations.execution_quality || [];
  const planCounts = access.plan_counts || {};
  const paymentCounts = billing.counts || {};
  const totalTrades = Number(metrics.total_trades || 0);
  const wins = Math.max(0, Math.round(totalTrades * Number(metrics.win_rate || 0)));
  const losses = Math.max(0, totalTrades - wins);
  const winShare = totalTrades > 0 ? wins / totalTrades : 0;

  return (
    <div className="page-shell">
      <div className="page-heading">
        <div>
          <div className="eyebrow accent">SYSTEM OVERVIEW / 01</div>
          <h1 className="page-title">Command Center</h1>
          <p className="page-subtitle">A deterministic view of market state, execution posture, signal flow, and subscriber operations.</p>
        </div>
        <div className="page-actions">
          <span className="data-as-of"><Clock3 size={14} /> {loading ? 'Refreshing…' : `Synced ${formatTime(lastRefreshAt || data?.generated_at)}`}</span>
          <button className="btn btn-icon-label" onClick={fetchOverview} disabled={loading} title="Refresh command center">
            <RefreshCw size={15} className={loading ? 'pulse-anim' : ''} /> {loading ? 'Refreshing' : 'Refresh'}
          </button>
        </div>
      </div>

      {error && <div className="notice notice-danger"><AlertTriangle size={16} /> {error}<button className="btn btn-small" onClick={fetchOverview}>Retry</button></div>}

      <div className="system-strip">
        <div className="status-chip"><span className="live-dot" /><span><small>MARKET SCANNER</small><strong>Online</strong></span></div>
        <div className="status-chip"><span className={`status-icon ${stateClass(watchdog.status)}`}><Activity size={15} /></span><span><small>WATCHDOG</small><strong>{display(watchdog.status)}</strong></span></div>
        <div className="status-chip"><span className={`status-icon ${stateClass(breaker.state)}`}><ShieldCheck size={15} /></span><span><small>ENTRY GUARD</small><strong>{display(breaker.state)}</strong></span></div>
        <div className="status-chip"><span className="status-icon accent-icon"><Users size={15} /></span><span><small>ACTIVE ACCESS</small><strong>{formatNumber(access.active_subscribers)} subscribers</strong></span></div>
      </div>

      <section className="hero-grid">
        <div className="data-panel performance-panel">
          <div className="panel-heading">
            <div><span className="eyebrow">PERFORMANCE LEDGER</span><h2>Execution performance</h2></div>
            <Link className="text-link" to="/journal">Open journal <ArrowUpRight size={14} /></Link>
          </div>
          <div className="performance-lead">
            <div><span className="metric-label">NET PNL</span><strong>{formatMoney(metrics.total_pnl)}</strong></div>
            <div className="performance-spark" aria-hidden="true"><i /><i /><i /><i /><i /><i /><i /><i /><i /></div>
          </div>
          <div className="metrics-grid four-up">
            <Metric label="Win rate" value={`${formatNumber(Number(metrics.win_rate || 0) * 100, 1)}%`} note={`${formatNumber(metrics.total_trades)} journaled trades`} icon={BarChart3} tone="success" />
            <Metric label="Profit factor" value={formatNumber(metrics.profit_factor, 2)} note="Gross profit / gross loss" icon={Gauge} />
            <Metric label="Expectancy" value={formatMoney(metrics.expectancy)} note="Average result per trade" icon={Crosshair} />
            <Metric label="Max drawdown" value={formatMoney(metrics.max_drawdown)} note={`Average RR ${formatNumber(metrics.avg_rr, 2)}R`} icon={AlertTriangle} tone="danger" />
          </div>
          <div className="outcome-visuals">
            <div>
              <span className="visual-title">Resolved outcome volume</span>
              <div className="outcome-bars">
                <div className="outcome-bar"><i style={{ height: `${Math.max(5, winShare * 100)}%` }} /><strong>{formatNumber(wins)}</strong><span>Wins</span></div>
                <div className="outcome-bar loss"><i style={{ height: `${Math.max(5, (1 - winShare) * 100)}%` }} /><strong>{formatNumber(losses)}</strong><span>Losses</span></div>
              </div>
            </div>
            <div>
              <span className="visual-title">Outcome split</span>
              <div className="outcome-donut-wrap">
                <div className="outcome-donut" style={{ '--chart-share': `${winShare * 360}deg` }} aria-label={`${formatNumber(winShare * 100, 1)} percent wins`} />
                <div className="donut-key"><span><i /> Wins {formatNumber(winShare * 100, 1)}%</span><span><i className="loss-key" /> Losses {formatNumber((1 - winShare) * 100, 1)}%</span></div>
              </div>
            </div>
          </div>
        </div>

        <div className="data-panel posture-panel">
          <div className="panel-heading"><div><span className="eyebrow">CONTROL POSTURE</span><h2>Execution guardrails</h2></div><Link className="icon-link" to="/operations" title="Open execution and risk"><ArrowUpRight size={16} /></Link></div>
          <div className="posture-state"><span className={`posture-marker ${stateClass(mode.status)}`}><ShieldCheck size={24} /></span><div><strong>{display(mode.label)}</strong><span>{display(mode.status_reason || mode.status)}</span></div></div>
          <div className="posture-list">
            <div><span>MT5 account</span><strong>{display(operations.account?.login)}</strong></div>
            <div><span>Broker server</span><strong>{display(operations.account?.server)}</strong></div>
            <div><span>Prop-firm profile</span><strong>{display(propFirm.selected_profile_id)}</strong></div>
            <div><span>Account verification</span><strong>{display(propFirm.status)}</strong></div>
            <div><span>Overall loss buffer</span><strong>{display(operations.loss_budget?.remaining_overall_buffer)}</strong></div>
            <div><span>Daily loss buffer</span><strong>{display(operations.loss_budget?.remaining_daily_buffer)}</strong></div>
          </div>
          <div className="panel-footnote"><span className={`badge ${stateClass(operations.reconciliation?.status)}`}>{display(operations.reconciliation?.status)}</span><span>Reconciliation state</span></div>
        </div>
      </section>

      <section className="dashboard-grid">
        <div className="data-panel table-panel">
          <div className="panel-heading"><div><span className="eyebrow">MARKET INTELLIGENCE</span><h2>Signal radar</h2></div><Link className="text-link" to="/market">Explore market <ArrowUpRight size={14} /></Link></div>
          <div className="table-scroll"><table className="premium-table dense-table"><thead><tr><th>Instrument</th><th>Bias</th><th>Distance</th><th>Status</th></tr></thead><tbody>
            {radarRows.length === 0 ? <tr><td colSpan="4" className="empty-cell">No live radar state published.</td></tr> : radarRows.map((row) => {
              const bullish = String(row.bias || '').toLowerCase().includes('bull');
              return <tr key={`${row.symbol}-${row.timeframe}`}><td><strong>{row.symbol}</strong><span className="cell-sub">{row.timeframe}</span></td><td><span className={bullish ? 'direction bullish' : 'direction bearish'}>{bullish ? '↑' : '↓'} {bullish ? 'Bullish' : 'Bearish'}</span></td><td>{display(row.distance_pips)} <span className="cell-sub">pips</span></td><td><span className={`badge ${row.status === 'Sweeping Now' ? 'badge-warning' : 'badge-outline'}`}>{display(row.status)}</span></td></tr>;
            })}
          </tbody></table></div>
        </div>

        <div className="data-panel flow-panel">
          <div className="panel-heading"><div><span className="eyebrow">ACCESS & BILLING</span><h2>Operating flow</h2></div><Link className="icon-link" to="/billing" title="Open billing and revenue"><ArrowUpRight size={16} /></Link></div>
          <div className="flow-stat"><span className="flow-icon teal"><Users size={17} /></span><div><strong>{formatNumber(access.active_subscribers)}</strong><span>active subscribers</span></div><span className="flow-meta">{formatNumber(access.expiring_within_7_days)} expiring soon</span></div>
          <div className="flow-stat"><span className="flow-icon gold"><WalletCards size={17} /></span><div><strong>{formatMoney(billing.confirmed_revenue_usd)}</strong><span>confirmed revenue</span></div><span className="flow-meta">{formatNumber(paymentCounts.confirmed)} confirmed</span></div>
          <div className="flow-stat"><span className="flow-icon coral"><CheckCircle2 size={17} /></span><div><strong>{display(data?.approvals?.pending_count)}</strong><span>approval queue</span></div><span className="flow-meta">awaiting action</span></div>
          <div className="plan-bar"><div><span>STANDARD</span><strong>{formatNumber(planCounts.standard)}</strong></div><div><span>PREMIUM</span><strong>{formatNumber(planCounts.premium)}</strong></div></div>
        </div>
      </section>

      <section className="dashboard-grid bottom-grid">
        <div className="data-panel table-panel">
          <div className="panel-heading"><div><span className="eyebrow">EXECUTION QUALITY</span><h2>Recent trade events</h2></div><Link className="text-link" to="/operations">View operations <ArrowUpRight size={14} /></Link></div>
          <div className="table-scroll"><table className="premium-table dense-table"><thead><tr><th>Time</th><th>Instrument</th><th>Event</th><th>Result</th><th>PnL</th></tr></thead><tbody>
            {qualityRows.length === 0 ? <tr><td colSpan="5" className="empty-cell">No execution-quality events recorded.</td></tr> : qualityRows.slice(0, 6).map((event) => <tr key={event.event_id}><td className="cell-sub">{formatTime(event.occurred_at)}</td><td><strong>{display(event.symbol)}</strong><span className="cell-sub">{display(event.timeframe)}</span></td><td>{display(event.action || event.event_type)}</td><td><span className={`badge ${stateClass(event.status)}`}>{display(event.status)}</span></td><td>{display(event.net_pnl)}</td></tr>)}
          </tbody></table></div>
        </div>
        <div className="data-panel insight-panel">
          <div className="panel-heading"><div><span className="eyebrow">DAILY TELEMETRY</span><h2>Signal flow</h2></div><Signal size={18} className="icon-accent" /></div>
          <div className="telemetry-number"><strong>{formatNumber(market.daily_stats?.total)}</strong><span>signals tracked today</span></div>
          <div className="telemetry-row"><span><span className="telemetry-dot success-dot" />Resolved wins</span><strong>{formatNumber(market.daily_stats?.success)}</strong></div>
          <div className="telemetry-row"><span><span className="telemetry-dot danger-dot" />Failed outcomes</span><strong>{formatNumber(market.daily_stats?.failed)}</strong></div>
          <div className="telemetry-row"><span><span className="telemetry-dot neutral-dot" />Watchdog failures</span><strong>{formatNumber(watchdog.consecutive_failures)}</strong></div>
          <div className="insight-callout"><Zap size={15} /><span>Routing is deterministic. A quiet radar is a valid system state, not an error.</span></div>
        </div>
      </section>
    </div>
  );
}
