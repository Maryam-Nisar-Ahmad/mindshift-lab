'use client';

import { useState, useCallback } from 'react';

export type Toast = {
  id: string;
  message: string;
  type: 'success' | 'error';
};

export default function useToast() {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const showToast = useCallback(
    (msg: string, type: 'success' | 'error') => {
      const id = Date.now().toString();
      const toast: Toast = { id, message: msg, type };
      setToasts((prev) => [...prev, toast]);
      setTimeout(() => removeToast(id), 3300);
    },
    [removeToast]
  );

  return { toasts, showToast, removeToast };
}
