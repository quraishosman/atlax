import React, { createContext, useState, useContext, useEffect, useRef } from 'react';
import axios from 'axios';

axios.defaults.withCredentials = true;

const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(false);
  const [profile, setProfile] = useState('Global View');
  const sessionVersion = useRef(0);

  const hydrateSession = async (clearOnFailure = true) => {
    const requestVersion = sessionVersion.current;
    try {
      const res = await axios.get('/api/auth/me');
      if (requestVersion === sessionVersion.current) {
        setUser(res.data.user ?? null);
      }
      return res.data.user ?? null;
    } catch (err) {
      if (clearOnFailure && requestVersion === sessionVersion.current) {
        setUser(null);
      }
      return null;
    } finally {
      if (requestVersion === sessionVersion.current) {
        setReady(true);
      }
    }
  };

  useEffect(() => {
    hydrateSession();
  }, []);

  const login = async (username, password) => {
    const formData = new URLSearchParams();
    formData.append('username', username);
    formData.append('password', password);

    try {
      const res = await axios.post('/api/auth/login', formData, {
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
      });

      sessionVersion.current += 1;
      setUser(res.data?.user ?? null);
      if (!res.data?.user) {
        await hydrateSession(false);
      }
      setReady(true);
      return true;
    } catch (err) {
      console.error(err);
      return false;
    }
  };

  const logout = async () => {
    try {
      await axios.post('/api/auth/logout');
    } catch (err) {
      console.error(err);
    } finally {
      sessionVersion.current += 1;
      setUser(null);
      setProfile('Global View');
      setReady(true);
    }
  };

  const signup = async (username, password) => {
    try {
      await axios.post('/api/auth/signup', { username, password });
      return true;
    } catch (err) {
      console.error(err);
      if (err.response && err.response.data && err.response.data.detail) {
        throw new Error(err.response.data.detail);
      }
      throw new Error("Signup failed");
    }
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: Boolean(user),
        ready,
        login,
        signup,
        logout,
        profile,
        setProfile
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => useContext(AuthContext);
