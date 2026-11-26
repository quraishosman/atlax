import React, { useState, useEffect } from 'react';
import axios from 'axios';
import * as jsyaml from 'js-yaml';
import { Save, AlertCircle, Check } from 'lucide-react';

const SettingsPage = () => {
  const [config, setConfig] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [status, setStatus] = useState({ type: '', message: '' });

  useEffect(() => {
    fetchConfig();
  }, []);

  const fetchConfig = async () => {
    try {
      const res = await axios.get('/api/config');
      const parsed = jsyaml.load(res.data.yaml);
      setConfig(parsed);
      setIsLoading(false);
    } catch (err) {
      console.error(err);
      setStatus({ type: 'error', message: 'Failed to load configuration.' });
      setIsLoading(false);
    }
  };

  const handleSave = async () => {
    setIsSaving(true);
    setStatus({ type: '', message: '' });
    try {
      const yamlContent = jsyaml.dump(config);
      await axios.post('/api/config', { yaml_content: yamlContent });
      setStatus({ type: 'success', message: 'Configuration saved successfully! Engine will reload on next cycle.' });
    } catch (err) {
      console.error(err);
      setStatus({ type: 'error', message: 'Failed to save configuration.' });
    } finally {
      setIsSaving(false);
    }
  };

  const handleProfileChange = (profileIndex, field, value) => {
    setConfig(prev => {
      const newProfiles = [...prev.strategy.profiles];
      newProfiles[profileIndex] = {
        ...newProfiles[profileIndex],
        [field]: value
      };
      return {
        ...prev,
        strategy: {
          ...prev.strategy,
          profiles: newProfiles
        }
      };
    });
  };

  if (isLoading) {
    return <div style={{ padding: '2rem', color: 'var(--text-main)' }}>Loading configuration matrix...</div>;
  }

  if (!config) {
    return <div style={{ padding: '2rem', color: 'var(--danger)' }}>Failed to load config.</div>;
  }

  return (
    <div className="page-shell">
      <div className="page-heading">
        <div>
          <div className="eyebrow accent">SYSTEM CONFIGURATION</div>
          <h1 className="page-title">Configuration</h1>
          <p className="page-subtitle">Adjust core strategy and risk parameters.</p>
        </div>
        <div className="page-actions">
          <button 
            onClick={handleSave} 
            disabled={isSaving}
            className="btn btn-gold btn-icon-label" 
          >
            <Save size={18} />
            {isSaving ? 'Deploying...' : 'Save & Deploy'}
          </button>
        </div>
      </div>

      {status.message && (
        <div className={`notice ${status.type === 'error' ? 'notice-danger' : 'notice-success'}`}>
          {status.type === 'error' ? <AlertCircle size={16} /> : <Check size={16} />}
          {status.message}
        </div>
      )}

      <div style={{ display: 'grid', gap: '14px' }}>
        {config.strategy.profiles.map((profileData, profileIndex) => (
          <div key={profileData.name} className="data-panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">PROFILE</span>
                <h2 style={{ textTransform: 'capitalize' }}>{profileData.name.replace('_', ' ')}</h2>
              </div>
            </div>
            
            <div className="metrics-grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))', gap: '14px', marginBottom: 0 }}>
              <div className="form-group">
                <label>Enabled</label>
                <select 
                  value={profileData.enabled}
                  onChange={(e) => handleProfileChange(profileIndex, 'enabled', e.target.value === 'true')}
                >
                  <option value={true}>Active</option>
                  <option value={false}>Disabled</option>
                </select>
              </div>

              <div className="form-group">
                <label>Min Confidence Threshold</label>
                <input 
                  type="number" 
                  step="0.1"
                  value={profileData.min_confidence_threshold}
                  onChange={(e) => handleProfileChange(profileIndex, 'min_confidence_threshold', parseFloat(e.target.value))}
                />
              </div>

              <div className="form-group">
                <label>Require Session Alignment</label>
                <select 
                  value={profileData.require_session_alignment}
                  onChange={(e) => handleProfileChange(profileIndex, 'require_session_alignment', e.target.value === 'true')}
                >
                  <option value={true}>Yes</option>
                  <option value={false}>No</option>
                </select>
              </div>

            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default SettingsPage;
