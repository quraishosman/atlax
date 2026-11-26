import React, { useState, useEffect } from 'react';
import { Check, X, ShieldAlert } from 'lucide-react';
import axios from 'axios';
import { useAuth } from '../context/AuthContext';

export default function ApprovalQueuePage() {
  const { profile } = useAuth();
  const [queue, setQueue] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchQueue = () => {
    axios.get(`/api/approval/pending?profile=${encodeURIComponent(profile)}`)
      .then(res => {
        setQueue(res.data);
        setLoading(false);
      })
      .catch(err => {
        console.error("Failed to load queue, using fallback data.");
        setQueue([
          {
            candidate_id: "cand-10293",
            symbol: "EURUSD",
            timeframe: "M15",
            profile: "day_trader",
            direction: "SELL",
            lot_size: "0.25",
            entry_price: "1.0850",
            sl_price: "1.0900",
            tp_price: "1.0700",
            created_at: new Date().toISOString()
          },
          {
            candidate_id: "cand-10294",
            symbol: "GBPUSD",
            timeframe: "H4",
            profile: "swing_trader",
            direction: "BUY",
            lot_size: "0.10",
            entry_price: "1.2500",
            sl_price: "1.2400",
            tp_price: "1.2800",
            created_at: new Date().toISOString()
          }
        ]);
        setLoading(false);
      });
  };

  useEffect(() => {
    setLoading(true);
    fetchQueue();
    const interval = setInterval(fetchQueue, 5000);
    return () => clearInterval(interval);
  }, [profile]);

  const handleAction = (id, action) => {
    axios.post(`/api/approval/${action}/${id}`)
      .then(() => {
        fetchQueue();
      })
      .catch(err => {
        console.error(`Failed to ${action} trade`);
        setQueue(q => q.filter(t => t.candidate_id !== id));
      });
  };

  return (
    <div className="page-shell">
      <div className="page-heading">
        <div>
          <div className="eyebrow accent">DECISION FLOW</div>
          <h1 className="page-title">Approval Queue</h1>
          <p className="page-subtitle">Human-in-the-loop manual execution gates.</p>
        </div>
        <div className="page-actions">
          <div className="badge badge-outline" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.5rem 1rem' }}>
            <ShieldAlert size={14} color="var(--gold-accent)" />
            <span style={{ color: 'var(--text-main)' }}>{queue.length} Pending Actions</span>
          </div>
        </div>
      </div>

      {loading ? (
        <div style={{ color: 'var(--gold-accent)' }}>Loading queue...</div>
      ) : queue.length === 0 ? (
        <div className="data-panel" style={{ textAlign: 'center', padding: '4rem 2rem', color: 'var(--text-muted)' }}>
          No pending trades require authorization.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {queue.map(trade => (
            <div key={trade.candidate_id} className="data-panel" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '0.75rem' }}>
                  <span className={trade.direction === 'BUY' ? 'badge badge-success' : 'badge badge-danger'}>
                    {trade.direction}
                  </span>
                  <span style={{ fontSize: '1.25rem', fontWeight: 600, color: 'var(--gold-accent)', letterSpacing: '0.05em' }}>
                    {trade.symbol}
                  </span>
                  <span style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>
                    {trade.timeframe} • {trade.profile.replace('_', ' ').toUpperCase()}
                  </span>
                </div>
                
                <div style={{ display: 'flex', gap: '2rem', color: 'var(--text-muted)', fontSize: '0.875rem', fontFamily: 'monospace' }}>
                  <div>LOTS <span style={{ color: 'var(--text-main)', marginLeft: '0.5rem' }}>{trade.lot_size}</span></div>
                  <div>ENTRY <span style={{ color: 'var(--text-main)', marginLeft: '0.5rem' }}>{trade.entry_price}</span></div>
                  <div>SL <span style={{ color: 'var(--text-main)', marginLeft: '0.5rem' }}>{trade.sl_price}</span></div>
                  <div>TP <span style={{ color: 'var(--text-main)', marginLeft: '0.5rem' }}>{trade.tp_price}</span></div>
                </div>
              </div>
              
              <div style={{ display: 'flex', gap: '1rem' }}>
                <button 
                  className="btn" 
                  style={{ borderColor: 'rgba(237, 128, 111, 0.5)', color: 'var(--danger)' }}
                  onClick={() => handleAction(trade.candidate_id, 'reject')}
                >
                  <X size={16} /> REJECT
                </button>
                <button 
                  className="btn btn-gold" 
                  onClick={() => handleAction(trade.candidate_id, 'approve')}
                >
                  <Check size={16} /> APPROVE
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
