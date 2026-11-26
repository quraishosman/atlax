import React from 'react';
import { BrowserRouter, Routes, Route, NavLink, Navigate, Link, useLocation } from 'react-router-dom';
import {
  BookOpen,
  CheckSquare,
  ChevronDown,
  CircleDollarSign,
  Gauge,
  LayoutDashboard,
  LogOut,
  Radar,
  Settings,
  ShieldAlert,
  Users,
} from 'lucide-react';
import { AuthProvider, useAuth } from './context/AuthContext';
import Login from './pages/Login';
import Signup from './pages/Signup';
import DashboardPage from './pages/Dashboard';
import ApprovalPage from './pages/ApprovalQueue';
import JournalPage from './pages/Journal';
import SettingsPage from './pages/Settings';
import SubscribersPage from './pages/Subscribers';
import MiniAppPage from './pages/MiniAppPage';
import OperationsPage from './pages/Operations';
import MarketPage from './pages/Market';
import BillingPage from './pages/Billing';

const AdminRoute = ({ children }) => {
  const { ready, user } = useAuth();
  if (!ready) {
    return null;
  }
  if (!user) {
    return <Navigate to="/login" replace />;
  }
  if (user.role !== 'admin') {
    return <Navigate to="/unauthorized" replace />;
  }
  return children;
};

const AccessDenied = () => (
  <div className="app-layout" style={{ minHeight: '100vh', alignItems: 'center', justifyContent: 'center' }}>
    <main className="main-content" style={{ maxWidth: '520px' }}>
      <div className="data-panel" style={{ padding: '2rem', textAlign: 'center' }}>
        <ShieldAlert size={32} color="var(--danger)" aria-hidden="true" />
        <h1 className="page-title">Administrator access required</h1>
        <p className="page-subtitle">This dashboard is restricted to ATLAX administrators.</p>
        <a className="btn btn-gold" href="/login">Return to sign in</a>
      </div>
    </main>
  </div>
);

const navGroups = [
  {
    label: 'Control plane',
    items: [
      { to: '/', label: 'Command Center', icon: LayoutDashboard },
      { to: '/market', label: 'Market Intelligence', icon: Radar },
      { to: '/journal', label: 'Signal Journal', icon: BookOpen },
    ],
  },
  {
    label: 'Decision flow',
    items: [
      { to: '/approvals', label: 'Approval Queue', icon: CheckSquare },
      { to: '/operations', label: 'Execution & Risk', icon: Gauge },
    ],
  },
  {
    label: 'Commercial',
    items: [
      { to: '/subscribers', label: 'Subscribers & Access', icon: Users },
      { to: '/billing', label: 'Billing & Revenue', icon: CircleDollarSign },
    ],
  },
];

const titleForPath = {
  '/': 'Command Center',
  '/market': 'Market Intelligence',
  '/journal': 'Signal Journal',
  '/approvals': 'Approval Queue',
  '/operations': 'Execution & Risk',
  '/subscribers': 'Subscribers & Access',
  '/billing': 'Billing & Revenue',
  '/settings': 'System Configuration',
};

const Layout = ({ children }) => {
  const { logout, profile, setProfile, user } = useAuth();
  const location = useLocation();
  const pageTitle = titleForPath[location.pathname] || 'ATLAX OS';
  
  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div className="sidebar-header">
          <Link to="/" className="brand-lockup">
            <span className="brand-mark">A</span>
            <span>
              <strong>ATLAX</strong>
              <small>TRADING OPERATING SYSTEM</small>
            </span>
          </Link>
          <div className="sidebar-status"><span className="live-dot" /> LIVE CONTROL PLANE</div>
          
          <div className="profile-picker">
            <span className="eyebrow">ACTIVE PROFILE</span>
            <div className="select-wrap">
              <select 
                value={profile} 
                onChange={(e) => setProfile(e.target.value)}
              >
                <option value="Global View">Global View</option>
                <option value="scalper">Scalper</option>
                <option value="day_trader">Day Trader</option>
                <option value="swing_trader">Swing Trader</option>
              </select>
              <ChevronDown size={14} />
            </div>
          </div>
        </div>
        
        <nav className="nav-menu">
          {navGroups.map((group) => (
            <div className="nav-group" key={group.label}>
              <span className="nav-group-label">{group.label}</span>
              {group.items.map(({ to, label, icon: Icon }) => (
                <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => isActive ? "nav-item active" : "nav-item"}>
                  <Icon size={17} />
                  <span>{label}</span>
                </NavLink>
              ))}
            </div>
          ))}
          
        </nav>
        <div className="nav-footer">
          <NavLink to="/settings" className={({ isActive }) => isActive ? "nav-item active" : "nav-item"}>
            <Settings size={17} />
            <span>System Configuration</span>
          </NavLink>
          
          <button className="nav-item" onClick={logout}>
            <LogOut size={17} />
            <span>Terminate Session</span>
          </button>
        </div>
        <div className="sidebar-footer">
          <span className="avatar">{String(user?.username || 'A').slice(0, 1).toUpperCase()}</span>
          <span><strong>{user?.username || 'Administrator'}</strong><small>Administrator</small></span>
        </div>
      </aside>
      
      <main className="main-content">
        <header className="topbar">
          <div className="breadcrumb"><span>ATLAX OS</span><b>/</b>{pageTitle}</div>
          <div className="topbar-actions">
            <span className="system-pill"><span className="live-dot" /> Scanner online</span>
            <span className="utc-clock">UTC {new Date().toISOString().slice(11, 16)}</span>
          </div>
        </header>
        {children}
      </main>
    </div>
  );
};

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/signup" element={<Signup />} />
          <Route path="/unauthorized" element={<AccessDenied />} />
          <Route path="/miniapp" element={<MiniAppPage />} />
          <Route path="/" element={
            <AdminRoute>
              <Layout>
                <DashboardPage />
              </Layout>
            </AdminRoute>
          } />
          <Route path="/approvals" element={
            <AdminRoute>
              <Layout>
                <ApprovalPage />
              </Layout>
            </AdminRoute>
          } />
          <Route path="/journal" element={
            <AdminRoute>
              <Layout>
                <JournalPage />
              </Layout>
            </AdminRoute>
          } />
          <Route path="/settings" element={
            <AdminRoute>
              <Layout>
                <SettingsPage />
              </Layout>
            </AdminRoute>
          } />
          <Route path="/subscribers" element={
            <AdminRoute>
              <Layout>
                <SubscribersPage />
              </Layout>
            </AdminRoute>
          } />
          <Route path="/operations" element={
            <AdminRoute>
              <Layout>
                <OperationsPage />
              </Layout>
            </AdminRoute>
          } />
          <Route path="/market" element={
            <AdminRoute>
              <Layout>
                <MarketPage />
              </Layout>
            </AdminRoute>
          } />
          <Route path="/billing" element={
            <AdminRoute>
              <Layout>
                <BillingPage />
              </Layout>
            </AdminRoute>
          } />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
