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

  const bg = type === 'success' ? '#3DBA6F' : '#EF4444';

  return (
    <div
      className="fixed top-4 right-4 z-50 rounded-lg px-5 py-3 flex items-center gap-3 text-white"
      style={{ backgroundColor: bg }}
      data-toast-id={id}
    >
      <span>{message}</span>
      <button type="button" onClick={onClose} aria-label="Close">
        X
      </button>
    </div>
  );
}
