import React, { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { useNavigate, Link } from 'react-router-dom';

export default function Login() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    const success = await login(username, password);
    if (success) {
      navigate('/');
    } else {
      setError('Invalid system credentials.');
    }
  };

  return (
    <div style={{
      display: 'flex', 
      alignItems: 'center', 
      justifyContent: 'center', 
      height: '100vh', 
      width: '100vw',
      backgroundColor: 'var(--bg-app)'
    }}>
      <div className="data-panel" style={{ width: '400px', padding: '3rem 2rem', textAlign: 'center' }}>
        <div className="brand-text" style={{ fontSize: '2rem', marginBottom: '0.5rem' }}>ATLAX OS</div>
        <div className="page-subtitle" style={{ marginBottom: '2rem' }}>Restricted Access Gateway</div>
        
        {error && (
          <div className="badge badge-danger" style={{ display: 'block', marginBottom: '1.5rem', padding: '0.75rem' }}>
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <input 
            type="text" 
            placeholder="Username" 
            value={username}
            onChange={e => setUsername(e.target.value)}
            style={{
              width: '100%',
              padding: '0.75rem',
              backgroundColor: 'var(--bg-app)',
              border: '1px solid var(--border-subtle)',
              color: 'var(--text-main)',
              borderRadius: '4px',
              fontFamily: 'var(--font-sans)',
              outline: 'none'
            }}
          />
          <input 
            type="password" 
            placeholder="Passphrase" 
            value={password}
            onChange={e => setPassword(e.target.value)}
            style={{
              width: '100%',
              padding: '0.75rem',
              backgroundColor: 'var(--bg-app)',
              border: '1px solid var(--border-subtle)',
              color: 'var(--text-main)',
              borderRadius: '4px',
              fontFamily: 'var(--font-sans)',
              outline: 'none'
            }}
          />
          <button type="submit" className="btn btn-gold" style={{ justifyContent: 'center', marginTop: '1rem', padding: '0.75rem' }}>
            AUTHORIZE
          </button>
        </form>
        
        <div style={{ textAlign: 'center', marginTop: '1.5rem', fontSize: '0.875rem' }}>
          <span style={{ color: 'var(--text-muted)' }}>First time setup? </span>
          <Link to="/signup" style={{ color: 'var(--gold-accent)', textDecoration: 'none' }}>Initialize System</Link>
        </div>
      </div>
    </div>
  );
}
