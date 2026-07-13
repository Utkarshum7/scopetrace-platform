import { useEffect, useRef } from 'react';

/**
 * Shared modal dialog shell -- overlay, role="dialog"/aria-modal, a
 * Tab/Shift+Tab focus trap, initial focus on open, focus restoration on
 * close, and Escape-to-close. Extracted from ApprovalModal (Phase 8,
 * 8a.2), which was the only modal in the app and is now its first
 * consumer; any future dialog gets this accessibility work for free.
 */
export const Modal = ({ isOpen, onClose, titleId, initialFocusRef, className = '', children }) => {
  const dialogRef = useRef(null);
  const previouslyFocusedRef = useRef(null);

  useEffect(() => {
    if (!isOpen) return undefined;
    previouslyFocusedRef.current = document.activeElement;
    const focusTimer = window.setTimeout(() => {
      (initialFocusRef?.current || dialogRef.current)?.focus();
    }, 0);
    return () => {
      window.clearTimeout(focusTimer);
      previouslyFocusedRef.current?.focus?.();
    };
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return undefined;
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== 'Tab') return;
      const focusable = dialogRef.current?.querySelectorAll(
        'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'
      );
      if (!focusable || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-slate-950/85 backdrop-blur-sm transition-opacity duration-300"
        onClick={onClose}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`relative z-10 transition-all duration-300 transform scale-100 ${className}`}
      >
        {children}
      </div>
    </div>
  );
};

export default Modal;
