import React, { useCallback, useEffect, useState } from 'react';
import { ArrowUpRight, CheckCircle2, Clock3, CreditCard, RefreshCw, Users, XCircle } from 'lucide-react';
import axios from 'axios';
import { Link } from 'react-router-dom';

const display = (value) => value === null || value === undefined || value === '' ? '—' : String(value);
const money = (value) => `$${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const formatTime = (value) => { if (!value) return '—'; const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString([], { dateStyle: 'short', timeStyle: 'short' }); };
const badge = (status) => ['confirmed'].includes(String(status).toLowerCase()) ? 'badge-success' : ['rejected', 'voided'].includes(String(status).toLowerCase()) ? 'badge-danger' : 'badge-outline';

export default function BillingPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lastRefreshAt, setLastRefreshAt] = useState(null);
  const load = useCallback(() => { setError(''); setLoading(true); axios.get('/api/admin/overview').then((response) => { setData(response.data); setLastRefreshAt(new Date()); }).catch(() => setError('Billing data is unavailable.')).finally(() => setLoading(false)); }, []);
  useEffect(() => { load(); const interval = setInterval(load, 15000); return () => clearInterval(interval); }, [load]);

  const billing = data?.billing || {};
  const counts = billing.counts || {};
  const access = data?.access || {};
  const payments = billing.recent || [];

  return (
    <div className="page-shell">
      <div className="page-heading"><div><div className="eyebrow accent">COMMERCIAL OPS / 03</div><h1 className="page-title">Billing & Revenue</h1><p className="page-subtitle">Payment lifecycle visibility and subscriber access health. Verification and approvals remain in the audited payment workflow.</p></div><div className="page-actions"><span className="data-as-of">{loading ? 'Refreshing…' : `Synced ${formatTime(lastRefreshAt || data?.generated_at)}`}</span><button className="btn btn-icon-label" onClick={load} disabled={loading}><RefreshCw size={15} className={loading ? 'pulse-anim' : ''} /> {loading ? 'Refreshing' : 'Refresh'}</button></div></div>
      {error && <div className="notice notice-danger">{error}</div>}
      <div className="metrics-grid four-up"><div className="metric-card"><div className="metric-header"><span className="metric-label">Confirmed revenue</span><CreditCard size={16} /></div><div className="metric-value gold">{money(billing.confirmed_revenue_usd)}</div><span className="metric-note">Confirmed ledger intents</span></div><div className="metric-card"><div className="metric-header"><span className="metric-label">Pending review</span><Clock3 size={16} /></div><div className="metric-value">{Number(counts.submitted || 0) + Number(counts.awaiting_payment || 0)}</div><span className="metric-note">Awaiting payment or review</span></div><div className="metric-card"><div className="metric-header"><span className="metric-label">Active access</span><Users size={16} /></div><div className="metric-value success">{display(access.active_subscribers)}</div><span className="metric-note">{display(access.expiring_within_7_days)} expiring within 7 days</span></div><div className="metric-card"><div className="metric-header"><span className="metric-label">Rejected / voided</span><XCircle size={16} /></div><div className="metric-value danger">{Number(counts.rejected || 0) + Number(counts.voided || 0)}</div><span className="metric-note">Requires no dashboard action</span></div></div>
      <section className="dashboard-grid billing-grid"><div className="data-panel table-panel"><div className="panel-heading"><div><span className="eyebrow">PAYMENT LEDGER</span><h2>Recent intents</h2></div><Link className="icon-link" to="/subscribers" title="Open subscribers and access"><ArrowUpRight size={16} /></Link></div><div className="table-scroll"><table className="premium-table dense-table"><thead><tr><th>Reference</th><th>User</th><th>Plan</th><th>Rail</th><th>Amount</th><th>Status</th><th>Created</th></tr></thead><tbody>{payments.length === 0 ? <tr><td colSpan="7" className="empty-cell">No payment intents recorded.</td></tr> : payments.map((item) => <tr key={item.reference}><td><strong>{display(item.reference)}</strong></td><td>{display(item.username)}</td><td><span className={String(item.plan).toLowerCase() === 'premium' ? 'plan-chip premium' : 'plan-chip'}>{display(item.plan)}</span></td><td>{display(item.method)}</td><td>{money(item.amount_usd)}</td><td><span className={`badge ${badge(item.status)}`}>{display(item.status)}</span></td><td className="cell-sub">{formatTime(item.created_at)}</td></tr>)}</tbody></table></div></div><div className="data-panel"><div className="panel-heading"><div><span className="eyebrow">PLAN MIX</span><h2>Access profile</h2></div><CheckCircle2 size={18} className="icon-accent" /></div><div className="plan-mix"><div className="plan-mix-row"><span className="plan-chip">STANDARD</span><strong>{display(access.plan_counts?.standard)}</strong></div><div className="plan-mix-row"><span className="plan-chip premium">PREMIUM</span><strong>{display(access.plan_counts?.premium)}</strong></div></div><p className="panel-note">The dashboard shows commercial state without exposing customer transaction proofs or wallet destinations.</p></div></section>
    </div>
  );
}
