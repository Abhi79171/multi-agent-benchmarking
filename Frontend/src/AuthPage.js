import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import RegisterForm from './RegisterForm';
import LoginForm from './LoginForm';
import './styles.css';

function AuthPage() {
  const [isRegistering, setIsRegistering] = useState(false);
  const [message, setMessage] = useState('');
  const navigate = useNavigate();

  const handleRegister = async (data) => {
    try {
      const response = await axios.post('http://localhost:5000/register', data);
      if (response.status === 201) {
        setMessage('Registration successful. Please log in.');
        setIsRegistering(false);
      }
    } catch (error) {
      setMessage(error.response.data.error);
    }
  };

  const handleLogin = async (data) => {
    try {
      const response = await axios.post('http://localhost:5000/login', data);
      if (response.status === 200) {
        navigate('/home');
      }
    } catch (error) {
      setMessage(error.response.data.error);
    }
  };

  return (
<div className="auth-container">
      {message && <p className="message">{message}</p>}
      {isRegistering ? (
        <RegisterForm onSubmit={handleRegister} />
      ) : (
        <LoginForm onSubmit={handleLogin} />
      )}
      <div
        className="toggle-link"
        onClick={() => setIsRegistering(!isRegistering)}
      >
        {isRegistering
          ? 'Already have an account? Log in'
          : "Don't have an account? Register"}
      </div>
    </div>
  );
}

export default AuthPage;
