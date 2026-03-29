'use client';

import { useEffect } from 'react';

type ToastProps = {
  id: string;
  message: string;
  type: 'success' | 'error';
  onClose: () => void;
};

export default function Toast({ id, message, type, onClose }: ToastProps) {
  useEffect(() => {
    const t = setTimeout(onClose, 3000);
    return () => clearTimeout(t);
  }, [onClose]);

  const bg = type === 'success' ? 'var(--garden-accent)' : 'var(--garden-danger)';

  return (
    <div
      className="fixed top-4 right-4 z-[100] flex items-center gap-3 rounded-2xl border-2 px-5 py-3 shadow-lg"
      style={{
        backgroundColor: bg,
        borderColor: 'var(--garden-border-strong)',
        color: 'var(--garden-cream)',
      }}
      data-toast-id={id}
    >
      <span>{message}</span>
      <button type="button" onClick={onClose} aria-label="Close" className="font-bold opacity-90 hover:opacity-100">
        X
      </button>
    </div>
  );
}
