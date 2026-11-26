import React, { useCallback, useEffect, useState } from 'react';
import { Activity, AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, RefreshCw, ShieldCheck, XCircle } from 'lucide-react';
import axios from 'axios';

const PAGE_SIZE = 10;

const stateClass = (value) => {
  const normalized = String(value || '').toUpperCase();
  if (['HEALTHY', 'CONNECTED', 'ACTIVE', 'ENABLED', 'NORMAL', 'AUTO-ENFORCED', 'FILLED'].includes(normalized)) return 'badge-success';
  if (['BLOCKED', 'DEGRADED', 'UNKNOWN', 'UNAVAILABLE', 'DISABLED', 'FAILED'].includes(normalized)) return 'badge-danger';
  return 'badge-outline';
};
const displayValue = (value) => value === null || value === undefined || value === '' ? 'UNKNOWN' : typeof value === 'object' ? JSON.stringify(value) : String(value);
const formatTime = (value) => { if (!value) return 'UNKNOWN'; const date = new Date(value); return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString(); };

function Pagination({ page, total, onChange }) {
  if (!total) return null;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const first = (page - 1) * PAGE_SIZE + 1;
  const last = Math.min(page * PAGE_SIZE, total);
  return <div className="pagination-bar"><span>Showing {first}–{last} of {total}</span><div className="pagination-controls"><button className="btn btn-small" onClick={() => onChange(page - 1)} disabled={page <= 1}><ChevronLeft size={14} /> Previous</button><span className="pagination-page">Page {page} of {pageCount}</span><button className="btn btn-small" onClick={() => onChange(page + 1)} disabled={page >= pageCount}>Next <ChevronRight size={14} /></button></div></div>;
}

export default function OperationsPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lastRefreshAt, setLastRefreshAt] = useState(null);
  const [healthPage, setHealthPage] = useState(1);
  const [qualityPage, setQualityPage] = useState(1);
  const [auditPage, setAuditPage] = useState(1);
  const [section, setSection] = useState('health');

  const fetchOperations = useCallback(() => {
    setLoading(true);
    setError('');
    axios.get('/api/admin/operations?limit=20')
      .then((res) => { setData(res.data); setLastRefreshAt(new Date()); })
      .catch(() => setError('Operations data is unavailable. Verify that the scanner and storage services are running.'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { fetchOperations(); const interval = setInterval(fetchOperations, 15000); return () => clearInterval(interval); }, [fetchOperations]);

  if (loading && !data) return <div className="page-shell"><h1 className="page-title">Operations Center</h1><p className="page-subtitle">Loading the administrative runtime view…</p></div>;
  if (error && !data) return <div className="page-shell"><h1 className="page-title">Operations Center</h1><p className="page-subtitle">Read-only health, risk, and execution observability.</p><div className="notice notice-danger"><AlertTriangle size={18} /> {error}<button className="btn btn-small" onClick={fetchOperations}>Retry</button></div></div>;

  const snapshot = data?.snapshot || {};
  const account = snapshot.account || {};
  const mode = data?.mode || {};
  const breaker = data?.circuit_breaker || {};
  const reconciliation = data?.reconciliation || {};
  const watchdog = data?.watchdog || {};
  const healthRows = Object.entries(watchdog.components || {});
  const quality = data?.execution_quality || [];
  const audit = data?.audit || [];
  const isBlocked = String(breaker.state || '').toUpperCase() === 'BLOCKED';
  const slice = (items, page) => items.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const healthVisible = slice(healthRows, Math.min(healthPage, Math.max(1, Math.ceil(healthRows.length / PAGE_SIZE))));
  const qualityVisible = slice(quality, Math.min(qualityPage, Math.max(1, Math.ceil(quality.length / PAGE_SIZE))));
  const auditVisible = slice(audit, Math.min(auditPage, Math.max(1, Math.ceil(audit.length / PAGE_SIZE))));

  return (
    <div className="page-shell operations-page">
      <div className="page-heading"><div><div className="eyebrow accent">RUNTIME CONTROL / 05</div><h1 className="page-title">Operations Center</h1><p className="page-subtitle">Read-only health, risk, account binding, and execution observability.</p></div><div className="page-actions"><span className="data-as-of">{loading ? 'Refreshing…' : `Synced ${formatTime(lastRefreshAt || data?.observed_at)}`}</span><button className="btn btn-icon-label" onClick={fetchOperations} disabled={loading}><RefreshCw size={15} className={loading ? 'pulse-anim' : ''} /> {loading ? 'Refreshing' : 'Refresh'}</button></div></div>

      {!data?.snapshot_available && <div className="notice"><AlertTriangle size={16} /> The scanner has not published a full operations snapshot yet. Health and ledger data below remain authoritative where available.</div>}
      {error && <div className="notice notice-danger"><AlertTriangle size={16} /> {error}</div>}

      <div className="metrics-grid">
        <div className="metric-card"><div className="metric-header"><span className="metric-label">Account mode</span><ShieldCheck size={16} /></div><div className="metric-value">{displayValue(mode.label)}</div><span className={`badge ${stateClass(mode.status)}`}>{displayValue(mode.status)}</span></div>
        <div className="metric-card"><div className="metric-header"><span className="metric-label">New-entry guard</span>{isBlocked ? <XCircle size={16} /> : <CheckCircle2 size={16} />}</div><div className="metric-value">{displayValue(breaker.state)}</div><span className="metric-note">{displayValue(breaker.reason_code)}</span></div>
        <div className="metric-card"><div className="metric-header"><span className="metric-label">Watchdog</span><Activity size={16} /></div><div className="metric-value">{displayValue(watchdog.status)}</div><span className="metric-note">{displayValue(watchdog.consecutive_failures)} consecutive failures</span></div>
        <div className="metric-card"><div className="metric-header"><span className="metric-label">Reconciliation</span><Activity size={16} /></div><div className="metric-value">{displayValue(reconciliation.status)}</div><span className="metric-note">Observed {formatTime(data?.observed_at)}</span></div>
      </div>

      <div className="ops-tabs" role="tablist" aria-label="Operations views"><button className={section === 'health' ? 'ops-tab active' : 'ops-tab'} onClick={() => setSection('health')}>Runtime health <span>{healthRows.length}</span></button><button className={section === 'account' ? 'ops-tab active' : 'ops-tab'} onClick={() => setSection('account')}>Account context</button><button className={section === 'quality' ? 'ops-tab active' : 'ops-tab'} onClick={() => setSection('quality')}>Execution quality <span>{quality.length}</span></button><button className={section === 'audit' ? 'ops-tab active' : 'ops-tab'} onClick={() => setSection('audit')}>Audit trail <span>{audit.length}</span></button></div>

      {section === 'health' && <div className="data-panel operations-panel"><div className="panel-heading"><div><span className="eyebrow">RUNTIME HEALTH</span><h2>Watchdog components</h2></div><span className="panel-caption">10 rows per page</span></div><div className="table-scroll"><table className="premium-table dense-table"><thead><tr><th>Component</th><th>Status</th><th>Reason</th><th>Last success</th></tr></thead><tbody>{healthRows.length === 0 ? <tr><td colSpan="4" className="empty-cell">Watchdog components are not available.</td></tr> : healthVisible.map(([name, item]) => <tr key={name}><td><strong>{name.replaceAll('_', ' ')}</strong></td><td><span className={`badge ${stateClass(item?.status)}`}>{displayValue(item?.status)}</span></td><td className="cell-sub">{displayValue(item?.reason)}</td><td className="cell-sub">{formatTime(item?.last_success_at)}</td></tr>)}</tbody></table></div><Pagination page={Math.min(healthPage, Math.max(1, Math.ceil(healthRows.length / PAGE_SIZE)))} total={healthRows.length} onChange={setHealthPage} /></div>}

      {section === 'account' && <div className="data-panel operations-panel"><div className="panel-heading"><div><span className="eyebrow">ACCOUNT & PROFILE</span><h2>Active execution context</h2></div></div><div className="context-grid"><div><span>MT5 login</span><strong>{displayValue(account.login)}</strong></div><div><span>Server</span><strong>{displayValue(account.server)}</strong></div><div><span>Currency</span><strong>{displayValue(account.currency)}</strong></div><div><span>Balance / Equity</span><strong>{displayValue(account.balance)} / {displayValue(account.equity)}</strong></div><div><span>Selected profile</span><strong>{displayValue(snapshot.profile?.profile_id || mode.raw)}</strong></div><div><span>Overall / daily buffer</span><strong>{displayValue(snapshot.loss_budget?.remaining_overall_buffer)} / {displayValue(snapshot.loss_budget?.remaining_daily_buffer)}</strong></div></div></div>}

      {section === 'quality' && <div className="data-panel operations-panel"><div className="panel-heading"><div><span className="eyebrow">EXECUTION QUALITY</span><h2>Recent trade events</h2></div><span className="panel-caption">10 rows per page</span></div><div className="table-scroll"><table className="premium-table dense-table"><thead><tr><th>Time</th><th>Instrument</th><th>Event</th><th>Status</th><th>Net PnL</th><th>Reason</th></tr></thead><tbody>{quality.length === 0 ? <tr><td colSpan="6" className="empty-cell">No execution-quality events recorded.</td></tr> : qualityVisible.map((event) => <tr key={event.event_id}><td className="cell-sub">{formatTime(event.occurred_at)}</td><td><strong>{displayValue(event.symbol)}</strong><span className="cell-sub">{displayValue(event.timeframe)}</span></td><td>{displayValue(event.action || event.event_type)}</td><td><span className={`badge ${stateClass(event.status)}`}>{displayValue(event.status)}</span></td><td>{displayValue(event.net_pnl)}</td><td className="cell-sub">{displayValue(event.reason)}</td></tr>)}</tbody></table></div><Pagination page={Math.min(qualityPage, Math.max(1, Math.ceil(quality.length / PAGE_SIZE)))} total={quality.length} onChange={setQualityPage} /></div>}

      {section === 'audit' && <div className="data-panel operations-panel"><div className="panel-heading"><div><span className="eyebrow">AUDIT TRAIL</span><h2>Recent administrative events</h2></div><span className="panel-caption">10 rows per page</span></div><div className="table-scroll"><table className="premium-table dense-table"><thead><tr><th>Time</th><th>Action</th><th>Result</th><th>Actor</th></tr></thead><tbody>{audit.length === 0 ? <tr><td colSpan="4" className="empty-cell">No audit events recorded.</td></tr> : auditVisible.map((event, index) => <tr key={`${event.timestamp}-${index}`}><td className="cell-sub">{formatTime(event.timestamp)}</td><td>{displayValue(event.action)}</td><td><span className={`badge ${stateClass(event.result)}`}>{displayValue(event.result)}</span></td><td>{displayValue(event.actor_chat_id)}</td></tr>)}</tbody></table></div><Pagination page={Math.min(auditPage, Math.max(1, Math.ceil(audit.length / PAGE_SIZE)))} total={audit.length} onChange={setAuditPage} /></div>}
    </div>
  );
}
