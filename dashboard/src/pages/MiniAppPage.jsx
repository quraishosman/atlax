import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  CircleHelp,
  RefreshCw,
  Save,
  ShieldCheck,
  UserRound,
  WalletCards,
} from 'lucide-react';
import axios from 'axios';
import '../miniapp.css';

const DEFAULT_PROFILE = {
  style: 'day_trader',
  timeframes: ['M15', 'M30', 'H1'],
  symbols: [],
  min_confidence: null,
  notifications: 'all',
  expires_at: null,
  subscription_source: '',
  subscription_status: 'inactive',
  active: false,
};

const formatDate = (value) => {
  if (!value) return 'Not set';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? 'Unknown' : parsed.toLocaleString();
};

const errorMessage = (error) => (
  error?.response?.data?.detail || error?.message || 'The terminal could not load.'
);

export default function MiniAppPage() {
  const [webApp, setWebApp] = useState(null);
  const [account, setAccount] = useState(null);
  const [profile, setProfile] = useState(DEFAULT_PROFILE);
  const [signals, setSignals] = useState([]);
  const [analytics, setAnalytics] = useState({ active: 0, tracked_today: 0, successful_today: 0, failed_today: 0 });
  const [payments, setPayments] = useState([]);
  const [draft, setDraft] = useState(DEFAULT_PROFILE);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [telegramUnavailable, setTelegramUnavailable] = useState(false);

  const loadTerminal = useCallback(async (telegramWebApp) => {
    if (!telegramWebApp?.initData) {
      setTelegramUnavailable(true);
      setLoading(false);
      return;
    }

    setError('');
    try {
      telegramWebApp.ready();
      telegramWebApp.expand();
      await axios.post('/api/miniapp/bootstrap', { init_data: telegramWebApp.initData });
      const [meResponse, signalResponse, paymentResponse] = await Promise.all([
        axios.get('/api/miniapp/me'),
        axios.get('/api/miniapp/signals?limit=30'),
        axios.get('/api/miniapp/payments'),
      ]);
      setAccount(meResponse.data.user);
      setProfile(meResponse.data.profile || DEFAULT_PROFILE);
      setDraft(meResponse.data.profile || DEFAULT_PROFILE);
      setSignals(signalResponse.data.signals || []);
      setAnalytics(signalResponse.data.analytics || {});
      setPayments(paymentResponse.data.payments || []);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    const telegramWebApp = window.Telegram?.WebApp || null;
    setWebApp(telegramWebApp);
    if (telegramWebApp?.themeParams) {
      const root = document.documentElement;
      const theme = telegramWebApp.themeParams;
      const safeColor = (value) => (typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value) ? value : null);
      const background = safeColor(theme.bg_color);
      const secondary = safeColor(theme.secondary_bg_color);
      if (background) root.style.setProperty('--miniapp-telegram-bg', background);
      if (secondary) root.style.setProperty('--miniapp-telegram-panel', secondary);
    }
    loadTerminal(telegramWebApp);
  }, [loadTerminal]);

  const refresh = () => {
    setRefreshing(true);
    loadTerminal(webApp);
  };

  const savePreferences = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError('');
    try {
      const symbols = typeof draft.symbols === 'string'
        ? draft.symbols.split(',').map((item) => item.trim()).filter(Boolean)
        : draft.symbols;
      const response = await axios.patch('/api/miniapp/preferences', {
        style: draft.style,
        symbols,
        min_confidence: draft.min_confidence === '' ? null : draft.min_confidence,
        notifications: draft.notifications,
      });
      const nextProfile = response.data.profile || draft;
      setProfile(nextProfile);
      setDraft(nextProfile);
      const signalResponse = await axios.get('/api/miniapp/signals?limit=30');
      setSignals(signalResponse.data.signals || []);
      setAnalytics(signalResponse.data.analytics || {});
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setSaving(false);
    }
  };

  const chartValues = useMemo(() => ([
    { label: 'Active', value: Number(analytics.active || 0), color: 'gold' },
    { label: 'Tracked', value: Number(analytics.tracked_today || 0), color: 'muted' },
    { label: 'Won', value: Number(analytics.successful_today || 0), color: 'success' },
    { label: 'Failed', value: Number(analytics.failed_today || 0), color: 'danger' },
  ]), [analytics]);
  const chartMax = Math.max(...chartValues.map((item) => item.value), 1);

  if (loading) {
    return <div className="miniapp-shell miniapp-state"><RefreshCw className="miniapp-spin" size={20} /><span>Loading ATLAX terminal...</span></div>;
  }

  if (telegramUnavailable) {
    return (
      <div className="miniapp-shell miniapp-state miniapp-state-blocked">
        <Activity size={28} color="var(--gold-accent)" />
        <h1>Open ATLAX inside Telegram</h1>
        <p>This terminal needs Telegram's signed Mini App session. Open the ATLAX launch button from the bot to continue.</p>
        <span className="miniapp-state-note">No browser token or unsigned Telegram profile is accepted.</span>
      </div>
    );
  }

  return (
    <div className="miniapp-shell">
      <header className="miniapp-header">
        <div>
          <p className="miniapp-eyebrow">ATLAX / TELEGRAM TERMINAL</p>
          <h1>Market Signal Desk</h1>
          <p className="miniapp-subtitle">Stored, explainable signal data for your active routing profile.</p>
        </div>
        <div className="miniapp-header-actions">
          <span className={profile.active ? 'miniapp-access active' : 'miniapp-access'}>
            <ShieldCheck size={14} /> {profile.active ? 'Access active' : 'Access inactive'}
          </span>
          <button className="miniapp-icon-button" type="button" onClick={refresh} disabled={refreshing} title="Refresh terminal data" aria-label="Refresh terminal data">
            <RefreshCw className={refreshing ? 'miniapp-spin' : ''} size={17} />
          </button>
        </div>
      </header>

      <div className="miniapp-status" role="status">
        <div><UserRound size={15} /><strong>{account?.display_name || account?.username || 'Telegram subscriber'}</strong><span>{account?.username ? `@${account.username}` : `ID ${account?.telegram_id || 'unknown'}`}</span></div>
        <div><span>Profile</span><strong>{profile.style.replace('_', ' ')}</strong><span>{profile.timeframes.join(' / ')}</span></div>
        <div><span>Access until</span><strong>{profile.active ? formatDate(profile.expires_at) : 'Subscribe in chat'}</strong></div>
      </div>

      {error && <div className="miniapp-error" role="alert"><CircleHelp size={16} />{error}</div>}

      {!profile.active ? (
        <section className="miniapp-empty miniapp-section">
          <WalletCards size={24} color="var(--gold-accent)" />
          <h2>Subscription required</h2>
          <p>Your Telegram identity is verified, but signal access is inactive. Use <strong>/subscribe</strong> or <strong>/buy</strong> in the ATLAX chat to begin the documented payment flow.</p>
        </section>
      ) : (
        <>
          <section className="miniapp-analytics-grid" aria-label="Signal analytics">
            <div className="miniapp-stat"><span>Live signals</span><strong>{analytics.active || 0}</strong><small>routed to you</small></div>
            <div className="miniapp-stat"><span>Tracked today</span><strong>{analytics.tracked_today || 0}</strong><small>stored predictions</small></div>
            <div className="miniapp-stat"><span>Confirmed wins</span><strong className="text-success">{analytics.successful_today || 0}</strong><small>outcome journal</small></div>
            <div className="miniapp-stat"><span>Invalidated</span><strong className="text-danger">{analytics.failed_today || 0}</strong><small>outcome journal</small></div>
          </section>

          <div className="miniapp-terminal-grid">
            <section className="miniapp-section miniapp-signals-panel">
              <div className="miniapp-section-heading">
                <div><p className="miniapp-eyebrow">REAL-TIME FEED</p><h2>Signal stream</h2></div>
                <span className="miniapp-section-count">{signals.length} visible</span>
              </div>
              {signals.length === 0 ? (
                <div className="miniapp-empty miniapp-empty-inline"><Activity size={20} /><p>No routed signals are active right now.</p></div>
              ) : (
                <div className="miniapp-signal-list">
                  {signals.map((signal) => {
                    const isBuy = signal.direction === 'BUY';
                    return (
                      <article className="miniapp-signal" key={signal.id}>
                        <div className={`miniapp-direction ${isBuy ? 'buy' : 'sell'}`} title={`${signal.direction} signal`}>
                          {isBuy ? <ArrowUpRight size={19} /> : <ArrowDownRight size={19} />}
                        </div>
                        <div className="miniapp-signal-main">
                          <div className="miniapp-signal-topline"><strong>{signal.symbol}</strong><span>{signal.timeframe || 'Timeframe pending'}</span><span>{signal.profile || 'Profile pending'}</span></div>
                          <div className="miniapp-signal-meta"><span>{formatDate(signal.timestamp)}</span><span>Entry {signal.entry_price || 'n/a'}</span><span>Invalidation {signal.invalidation || 'n/a'}</span></div>
                          <div className="miniapp-confidence"><span>Confidence {signal.confidence ?? 'n/a'}</span><div><i style={{ width: `${Math.min(100, Math.max(0, Number(signal.confidence) || 0))}%` }} /></div></div>
                        </div>
                        <div className="miniapp-signal-targets"><span>TP1 {signal.midpoint || 'n/a'}</span><span>TP2 {signal.target || 'n/a'}</span></div>
                      </article>
                    );
                  })}
                </div>
              )}
            </section>

            <aside className="miniapp-side-column">
              <section className="miniapp-section miniapp-chart-panel">
                <div className="miniapp-section-heading"><div><p className="miniapp-eyebrow">OUTCOME SNAPSHOT</p><h2>Today</h2></div><Activity size={17} color="var(--gold-accent)" /></div>
                <div className="miniapp-bar-chart" aria-label="Today's signal outcome chart">
                  {chartValues.map((item) => <div className="miniapp-bar-item" key={item.label}><div className={`miniapp-bar ${item.color}`} style={{ height: `${Math.max(8, (item.value / chartMax) * 100)}%` }} /><strong>{item.value}</strong><span>{item.label}</span></div>)}
                </div>
              </section>

              <section className="miniapp-section">
                <div className="miniapp-section-heading"><div><p className="miniapp-eyebrow">ROUTING</p><h2>Preferences</h2></div></div>
                <form className="miniapp-form" onSubmit={savePreferences}>
                  <label className="miniapp-field"><span>Trading style</span><select value={draft.style} onChange={(event) => setDraft({ ...draft, style: event.target.value })}><option value="scalper">Scalper</option><option value="day_trader">Day trader</option><option value="swing_trader">Swing trader</option></select></label>
                  <label className="miniapp-field"><span>Symbols</span><input value={Array.isArray(draft.symbols) ? draft.symbols.join(', ') : draft.symbols} onChange={(event) => setDraft({ ...draft, symbols: event.target.value })} placeholder="All routed symbols" /></label>
                  <label className="miniapp-field"><span>Minimum confidence</span><select value={draft.min_confidence ?? ''} onChange={(event) => setDraft({ ...draft, min_confidence: event.target.value === '' ? null : Number(event.target.value) })}><option value="">Profile default</option><option value="60">60+</option><option value="70">70+</option><option value="80">80+</option><option value="90">90+</option></select></label>
                  <label className="miniapp-field"><span>Notifications</span><select value={draft.notifications} onChange={(event) => setDraft({ ...draft, notifications: event.target.value })}><option value="all">All updates</option><option value="signals_only">New signals only</option><option value="minimal">Signals and invalidations</option></select></label>
                  <button className="miniapp-primary-button" type="submit" disabled={saving}><Save size={16} />{saving ? 'Saving...' : 'Save preferences'}</button>
                </form>
              </section>

              <section className="miniapp-section">
                <div className="miniapp-section-heading"><div><p className="miniapp-eyebrow">ACCOUNT</p><h2>Payment history</h2></div><WalletCards size={17} color="var(--gold-accent)" /></div>
                {payments.length === 0 ? <p className="miniapp-muted">No payment intents recorded.</p> : <div className="miniapp-payment-list">{payments.map((payment) => <div className="miniapp-payment" key={payment.reference}><div><strong>{payment.method.toUpperCase()} · ${payment.amount_usd}</strong><span>{payment.reference}</span></div><span className={`miniapp-payment-status ${payment.status}`}>{payment.status.replace('_', ' ')}</span></div>)}</div>}
                <p className="miniapp-note">Payment confirmation remains an admin-reviewed Telegram workflow.</p>
              </section>
            </aside>
          </div>
        </>
      )}
    </div>
  );
}
