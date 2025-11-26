import React from 'react';
import './Dialog.css';

const Dialog = ({ isOpen, onClose, title, message, type }) => {
  if (!isOpen) return null;

  return (
    <div className="dialog-overlay" onClick={onClose}>
      <div className="dialog-content" onClick={(e) => e.stopPropagation()}>
        <div className={`dialog-icon ${type}`}>
          {type === 'success' ? (
            <i className="fas fa-check-circle"></i>
          ) : (
            <i className="fas fa-exclamation-circle"></i>
          )}
        </div>
        <h3 className="dialog-title">{title}</h3>
        <p className="dialog-message">{message}</p>
        <button className="dialog-button" onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  );
};

export default Dialog;
