import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { Shield, Lock, User, Key, AlertCircle } from 'lucide-react';
import { useAuth } from '../context/AuthContext';

const Signup = () => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  
  const { signup } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    
    if (password !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }
    
    setIsLoading(true);
    
    try {
      const success = await signup(username, password);
      if (success) {
        navigate('/login');
      } else {
        setError('Signup failed. Username might be taken.');
      }
    } catch (err) {
      setError(err.message || 'An error occurred during signup');
    } finally {
      setIsLoading(false);
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
        <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '1rem' }}>
          <Shield size={40} color="var(--gold-accent)" />
        </div>
        <div className="brand-text" style={{ fontSize: '2rem', marginBottom: '0.5rem' }}>ATLAX OS</div>
        <div className="page-subtitle" style={{ marginBottom: '2rem' }}>Initialize Admin Account</div>
        
        {error && (
          <div className="badge badge-danger" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.5rem', marginBottom: '1.5rem', padding: '0.75rem' }}>
            <AlertCircle size={16} />
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div style={{ position: 'relative' }}>
            <User size={18} style={{ position: 'absolute', left: '1rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Username"
              required
              style={{
                width: '100%',
                padding: '0.75rem 0.75rem 0.75rem 3rem',
                backgroundColor: 'var(--bg-app)',
                border: '1px solid var(--border-subtle)',
                color: 'var(--text-main)',
                borderRadius: '4px',
                fontFamily: 'var(--font-sans)',
                outline: 'none',
                boxSizing: 'border-box'
              }}
            />
          </div>

          <div style={{ position: 'relative' }}>
            <Lock size={18} style={{ position: 'absolute', left: '1rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Create a password"
              required
              style={{
                width: '100%',
                padding: '0.75rem 0.75rem 0.75rem 3rem',
                backgroundColor: 'var(--bg-app)',
                border: '1px solid var(--border-subtle)',
                color: 'var(--text-main)',
                borderRadius: '4px',
                fontFamily: 'var(--font-sans)',
                outline: 'none',
                boxSizing: 'border-box'
              }}
            />
          </div>
          
          <div style={{ position: 'relative' }}>
            <Key size={18} style={{ position: 'absolute', left: '1rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
            <input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Confirm your password"
              required
              style={{
                width: '100%',
                padding: '0.75rem 0.75rem 0.75rem 3rem',
                backgroundColor: 'var(--bg-app)',
                border: '1px solid var(--border-subtle)',
                color: 'var(--text-main)',
                borderRadius: '4px',
                fontFamily: 'var(--font-sans)',
                outline: 'none',
                boxSizing: 'border-box'
              }}
            />
          </div>

          <button type="submit" className="btn btn-gold" disabled={isLoading} style={{ justifyContent: 'center', marginTop: '1rem', padding: '0.75rem' }}>
            {isLoading ? 'CREATING...' : 'INITIALIZE SYSTEM'}
          </button>
        </form>
        
        <div style={{ textAlign: 'center', marginTop: '1.5rem', fontSize: '0.875rem' }}>
          <span style={{ color: 'var(--text-muted)' }}>Already have an account? </span>
          <Link to="/login" style={{ color: 'var(--gold-accent)', textDecoration: 'none' }}>Access Terminal</Link>
        </div>
      </div>
    </div>
  );
};

export default Signup;
