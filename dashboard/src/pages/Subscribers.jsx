import React, { useState, useEffect } from 'react';
import { Users, Key, Plus, Trash2, Copy, Shield } from 'lucide-react';
import axios from 'axios';

export default function SubscribersPage() {
  const [subscribers, setSubscribers] = useState([]);
  const [codes, setCodes]             = useState([]);
  const [days, setDays]               = useState(30);
  const [label, setLabel]             = useState('');
  const [newCode, setNewCode]         = useState(null);
  const [loading, setLoading]         = useState(true);
  const [copied, setCopied]           = useState('');

  const STYLE_LABELS = {
    scalper:      '🏃 Scalper (M5)',
    day_trader:   '📅 Day Trader (M15/H1)',
    swing_trader: '🌊 Swing Trader (H4/D1)',
  };

  const load = () => {
    setLoading(true);
    Promise.all([
      axios.get('/api/subscribers'),
      axios.get('/api/invite-codes'),
    ]).then(([subRes, codeRes]) => {
      setSubscribers(subRes.data);
      setCodes(Array.isArray(codeRes.data) ? codeRes.data : Object.values(codeRes.data));
      setLoading(false);
    }).catch(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const generateCode = () => {
    axios.post('/api/invite-codes/generate', { days_valid: days, label })
      .then(res => {
        setNewCode(res.data.code);
        load();
      });
  };

  const revokeCode = (code) => {
    if (!window.confirm(`Revoke code ${code}?`)) return;
    axios.delete(`/api/invite-codes/${code}`).then(load);
  };

  const banSubscriber = (chat_id, username) => {
    if (!window.confirm(`Deactivate @${username}?`)) return;
    axios.delete(`/api/subscribers/${chat_id}`).then(load);
  };

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text);
    setCopied(text);
    setTimeout(() => setCopied(''), 2000);
  };

  const activeCount   = subscribers.filter(s => s.active).length;
  const inactiveCount = subscribers.filter(s => !s.active).length;
  const usedCodes     = codes.filter(c => c.used_by).length;
  const availCodes    = codes.filter(c => !c.used_by).length;

  return (
    <div>
      <h1 className="page-title">Subscriber Management</h1>
      <p className="page-subtitle">Manage invite codes and monitor your ATLAX signal subscribers.</p>

      {/* Summary Cards */}
      <div className="metrics-grid" style={{ marginBottom: '28px' }}>
        <div className="metric-card">
          <div className="metric-header">
            <span className="metric-label">Active Subscribers</span>
            <Users size={16} color="var(--gold-accent)" />
          </div>
          <div className="metric-value gold">{activeCount}</div>
        </div>
        <div className="metric-card">
          <div className="metric-header">
            <span className="metric-label">Inactive / Banned</span>
            <Shield size={16} color="var(--danger)" />
          </div>
          <div className="metric-value danger">{inactiveCount}</div>
        </div>
        <div className="metric-card">
          <div className="metric-header">
            <span className="metric-label">Available Codes</span>
            <Key size={16} color="var(--text-muted)" />
          </div>
          <div className="metric-value">{availCodes}</div>
        </div>
        <div className="metric-card">
          <div className="metric-header">
            <span className="metric-label">Used Codes</span>
            <Key size={16} color="var(--text-muted)" />
          </div>
          <div className="metric-value">{usedCodes}</div>
        </div>
      </div>

      {/* Generate Invite Code */}
      <div className="data-panel" style={{ marginBottom: '24px' }}>
        <h3 className="table-header" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Key size={16} color="var(--gold-accent)" /> Generate Invite Code
        </h3>
        <div style={{ display: 'flex', gap: '12px', alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: '12px' }}>
          <div>
            <label className="metric-label" style={{ display: 'block', marginBottom: '4px' }}>Valid Days</label>
            <input
              id="days-input"
              type="number"
              value={days}
              min={1}
              onChange={e => setDays(Number(e.target.value))}
              style={{
                background: 'var(--surface-raised)', border: '1px solid var(--border)',
                color: 'var(--text-bright)', borderRadius: '6px', padding: '8px 12px',
                width: '100px', fontSize: '0.9rem'
              }}
            />
          </div>
          <div style={{ flex: 1, minWidth: '180px' }}>
            <label className="metric-label" style={{ display: 'block', marginBottom: '4px' }}>Label (optional)</label>
            <input
              id="label-input"
              type="text"
              value={label}
              placeholder="e.g. 30-day VIP"
              onChange={e => setLabel(e.target.value)}
              style={{
                background: 'var(--surface-raised)', border: '1px solid var(--border)',
                color: 'var(--text-bright)', borderRadius: '6px', padding: '8px 12px',
                width: '100%', fontSize: '0.9rem'
              }}
            />
          </div>
          <button
            id="gen-code-btn"
            onClick={generateCode}
            style={{
              background: 'linear-gradient(135deg, var(--gold-accent), #b8902a)',
              border: 'none', color: '#000', fontWeight: 700,
              padding: '8px 20px', borderRadius: '6px', cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: '6px'
            }}
          >
            <Plus size={14} /> Generate
          </button>
        </div>
        {newCode && (
          <div style={{
            background: 'rgba(212,175,55,0.1)', border: '1px solid var(--gold-accent)',
            borderRadius: '8px', padding: '12px 16px', display: 'flex', alignItems: 'center',
            justifyContent: 'space-between', gap: '12px'
          }}>
            <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>New code ready to share:</span>
            <code style={{ color: 'var(--gold-accent)', fontWeight: 700, fontSize: '1rem', letterSpacing: '2px' }}>{newCode}</code>
            <button
              onClick={() => copyToClipboard(newCode)}
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: copied === newCode ? 'var(--success)' : 'var(--text-muted)' }}
            >
              <Copy size={16} /> {copied === newCode ? ' Copied!' : ' Copy'}
            </button>
          </div>
        )}
      </div>

      {/* Invite Codes Table */}
      <div className="data-panel" style={{ marginBottom: '24px' }}>
        <h3 className="table-header">Invite Codes</h3>
        {codes.length === 0 ? (
          <p style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '20px' }}>No invite codes yet. Generate one above.</p>
        ) : (
          <table className="premium-table">
            <thead>
              <tr>
                <th>Code</th>
                <th>Label</th>
                <th>Days</th>
                <th>Status</th>
                <th>Expires</th>
                <th style={{ textAlign: 'right' }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {codes.map(c => (
                <tr key={c.code}>
                  <td>
                    <code style={{ color: 'var(--gold-accent)', fontSize: '0.85rem' }}>{c.code}</code>
                    <button
                      onClick={() => copyToClipboard(c.code)}
                      style={{ background: 'none', border: 'none', cursor: 'pointer', color: copied === c.code ? 'var(--success)' : 'var(--text-muted)', marginLeft: '6px' }}
                    >
                      <Copy size={12} />
                    </button>
                  </td>
                  <td style={{ color: 'var(--text-muted)' }}>{c.label || '—'}</td>
                  <td>{c.days_valid}d</td>
                  <td>
                    <span style={{
                      padding: '2px 8px', borderRadius: '4px', fontSize: '0.8rem', fontWeight: 600,
                      background: c.used_by ? 'rgba(255,100,100,0.15)' : 'rgba(80,200,120,0.15)',
                      color: c.used_by ? 'var(--danger)' : 'var(--success)'
                    }}>
                      {c.used_by ? 'Used' : 'Available'}
                    </span>
                  </td>
                  <td style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                    {c.expires ? new Date(c.expires).toLocaleDateString() : '—'}
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    <button
                      onClick={() => revokeCode(c.code)}
                      style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--danger)' }}
                    >
                      <Trash2 size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Subscribers Table */}
      <div className="data-panel">
        <h3 className="table-header">Subscribers</h3>
        {subscribers.length === 0 ? (
          <p style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '20px' }}>No subscribers yet. Share an invite code to get started.</p>
        ) : (
          <table className="premium-table">
            <thead>
              <tr>
                <th>Username</th>
                <th>Trading Style</th>
                <th>Timeframes</th>
                <th>Status</th>
                <th>Joined</th>
                <th style={{ textAlign: 'right' }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {subscribers.map(s => (
                <tr key={s.chat_id}>
                  <td style={{ color: 'var(--text-bright)', fontWeight: 600 }}>@{s.username}</td>
                  <td>{STYLE_LABELS[s.style] || s.style}</td>
                  <td style={{ color: 'var(--gold-accent)', fontSize: '0.85rem' }}>{(s.timeframes || []).join(', ')}</td>
                  <td>
                    <span style={{
                      padding: '2px 8px', borderRadius: '4px', fontSize: '0.8rem', fontWeight: 600,
                      background: s.active ? 'rgba(80,200,120,0.15)' : 'rgba(255,100,100,0.15)',
                      color: s.active ? 'var(--success)' : 'var(--danger)'
                    }}>
                      {s.active ? 'Active' : 'Banned'}
                    </span>
                  </td>
                  <td style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                    {s.joined ? new Date(s.joined).toLocaleDateString() : '—'}
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    {s.active && (
                      <button
                        onClick={() => banSubscriber(s.chat_id, s.username)}
                        title="Deactivate subscriber"
                        style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--danger)' }}
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
