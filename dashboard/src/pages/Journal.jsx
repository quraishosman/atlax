import React, { useEffect, useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, Filter, RefreshCw, Search } from 'lucide-react';
import axios from 'axios';
import { useAuth } from '../context/AuthContext';

const PAGE_SIZE = 10;

const formatLabel = (value) => String(value || '—').replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
const formatDate = (value) => {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleDateString();
};
const formatMoney = (value) => {
  const number = Number(value);
  return Number.isFinite(number) ? `${number >= 0 ? '+' : ''}${number.toFixed(2)}` : '—';
};

function Pagination({ page, pageCount, total, onChange }) {
  if (total === 0) return null;
  const first = (page - 1) * PAGE_SIZE + 1;
  const last = Math.min(page * PAGE_SIZE, total);
  return (
    <div className="pagination-bar">
      <span>Showing {first}–{last} of {total}</span>
      <div className="pagination-controls">
        <button className="btn btn-small" onClick={() => onChange(page - 1)} disabled={page <= 1} title="Previous page"><ChevronLeft size={14} /> Previous</button>
        <span className="pagination-page">Page {page} of {pageCount}</span>
        <button className="btn btn-small" onClick={() => onChange(page + 1)} disabled={page >= pageCount} title="Next page">Next <ChevronRight size={14} /></button>
      </div>
    </div>
  );
}

export default function JournalPage() {
  const { profile } = useAuth();
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [showFilters, setShowFilters] = useState(false);
  const [search, setSearch] = useState('');
  const [direction, setDirection] = useState('all');
  const [outcome, setOutcome] = useState('all');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [page, setPage] = useState(1);

  const load = () => {
    setRefreshing(true);
    setError('');
    axios.get(`/api/journal?profile=${encodeURIComponent(profile)}`)
      .then((response) => setEntries(Array.isArray(response.data) ? response.data : []))
      .catch(() => { setEntries([]); setError('Journal data is unavailable. Check the API and storage services.'); })
      .finally(() => { setLoading(false); setRefreshing(false); });
  };

  useEffect(() => { setLoading(true); load(); }, [profile]);
  useEffect(() => { setPage(1); }, [profile, search, direction, outcome, dateFrom, dateTo]);

  const filteredEntries = useMemo(() => {
    const query = search.trim().toLowerCase();
    return entries.filter((entry) => {
      const entryDate = entry.closed_at ? new Date(entry.closed_at) : null;
      const dateValue = entryDate && !Number.isNaN(entryDate.getTime()) ? entryDate.toISOString().slice(0, 10) : '';
      const searchable = `${entry.symbol || ''} ${entry.profile || ''} ${entry.exit_reason || ''} ${entry.direction || ''}`.toLowerCase();
      const matchesSearch = !query || searchable.includes(query);
      const matchesDirection = direction === 'all' || String(entry.direction || '').toUpperCase() === direction;
      const pnl = Number(entry.pnl);
      const matchesOutcome = outcome === 'all' || (outcome === 'wins' && pnl > 0) || (outcome === 'losses' && pnl <= 0);
      const matchesFrom = !dateFrom || dateValue >= dateFrom;
      const matchesTo = !dateTo || dateValue <= dateTo;
      return matchesSearch && matchesDirection && matchesOutcome && matchesFrom && matchesTo;
    });
  }, [entries, search, direction, outcome, dateFrom, dateTo]);

  const pageCount = Math.max(1, Math.ceil(filteredEntries.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const visibleEntries = filteredEntries.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  const resetFilters = () => {
    setSearch(''); setDirection('all'); setOutcome('all'); setDateFrom(''); setDateTo('');
  };

  return (
    <div className="page-shell journal-page">
      <div className="page-heading">
        <div><div className="eyebrow accent">PERFORMANCE LEDGER / 04</div><h1 className="page-title">Signal Journal</h1><p className="page-subtitle">Immutable historical ledger of executed setups. Browse ten records at a time.</p></div>
        <div className="page-actions"><span className="data-as-of">{loading || refreshing ? 'Refreshing…' : `${filteredEntries.length} matching records`}</span><button className={`btn btn-icon-label ${showFilters ? 'btn-gold' : ''}`} onClick={() => setShowFilters((value) => !value)}><Filter size={15} /> {showFilters ? 'Hide filters' : 'Filter logs'}</button><button className="btn btn-icon-label" onClick={load} disabled={refreshing}><RefreshCw size={15} className={refreshing ? 'pulse-anim' : ''} /> {refreshing ? 'Refreshing' : 'Refresh'}</button></div>
      </div>

      {error && <div className="notice notice-danger">{error}<button className="btn btn-small" onClick={load}>Retry</button></div>}
      {showFilters && <div className="journal-filter-panel"><label className="journal-filter-field"><span>Search</span><div className="input-with-icon"><Search size={14} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Pair, profile, exit reason" /></div></label><label className="journal-filter-field"><span>Direction</span><select value={direction} onChange={(event) => setDirection(event.target.value)}><option value="all">All directions</option><option value="BUY">Buy</option><option value="SELL">Sell</option></select></label><label className="journal-filter-field"><span>Outcome</span><select value={outcome} onChange={(event) => setOutcome(event.target.value)}><option value="all">All outcomes</option><option value="wins">Wins</option><option value="losses">Losses</option></select></label><label className="journal-filter-field"><span>From</span><input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} /></label><label className="journal-filter-field"><span>To</span><input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} /></label><button className="btn btn-small" onClick={resetFilters}>Reset</button></div>}

      <div className="data-panel journal-panel">
        {loading ? <div className="table-empty-state">Synchronizing journal…</div> : <>
          <div className="table-scroll"><table className="premium-table journal-table"><thead><tr><th>Closed</th><th>Instrument</th><th>Timeframe</th><th>Direction</th><th>Profile</th><th>Exit reason</th><th className="numeric-cell">Net PnL</th></tr></thead><tbody>
            {visibleEntries.length === 0 ? <tr><td colSpan="7" className="empty-cell">No journal records match the current filters.</td></tr> : visibleEntries.map((entry) => { const pnl = Number(entry.pnl); return <tr key={entry.record_id || entry.candidate_id}><td className="cell-sub">{formatDate(entry.closed_at)}</td><td><strong>{entry.symbol || '—'}</strong></td><td className="cell-sub">{entry.timeframe || '—'}</td><td><span className={String(entry.direction).toUpperCase() === 'BUY' ? 'badge badge-success' : 'badge badge-danger'}>{entry.direction || 'UNKNOWN'}</span></td><td><span className="badge badge-outline">{formatLabel(entry.profile)}</span></td><td className="cell-sub">{formatLabel(entry.exit_reason)}</td><td className={`numeric-cell pnl-cell ${pnl >= 0 ? 'text-success' : 'text-danger'}`}>{formatMoney(entry.pnl)}</td></tr>; })}
          </tbody></table></div>
          <Pagination page={safePage} pageCount={pageCount} total={filteredEntries.length} onChange={setPage} />
        </>}
      </div>
    </div>
  );
}
