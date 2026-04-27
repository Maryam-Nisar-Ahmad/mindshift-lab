'use client';

import { useRouter } from 'next/navigation';
import { useState, useEffect } from 'react';
import { listProjects, createProject } from '@/lib/api';
import { encodeProjectIdForRoute } from '@/lib/projectId';
import useToast from '@/hooks/useToast';
import Toast from '@/components/Toast';

type Project = { id: string; title: string; current_step?: number; mtime?: number };

export default function Page() {
  const router = useRouter();
  const { toasts, showToast, removeToast } = useToast();
  const [modalOpen, setModalOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [title, setTitle] = useState('');
  const [imageProvider, setImageProvider] = useState<'midjourney' | 'grok'>('grok');
  const [projects, setProjects] = useState<Project[]>([]);

  useEffect(() => {
    if (drawerOpen) {
      listProjects()
        .then(setProjects)
        .catch((err) => {
          setProjects([]);
          showToast(err instanceof Error ? err.message : 'Request failed', 'error');
        });
    }
  }, [drawerOpen, showToast]);

  async function handleCreate() {
    const t = title.trim();
    if (!t) return;
    try {
      const result = await createProject(t, imageProvider);
      setModalOpen(false);
      setTitle('');
      router.push('/project/' + encodeProjectIdForRoute(result.id));
      showToast('Project created', 'success');
    } catch {
      showToast('Failed to create project', 'error');
    }
  }

  return (
    <div
      className="fixed inset-0 flex items-center justify-center overflow-y-auto"
      style={{ backgroundColor: 'var(--garden-surface)' }}
    >
      <div className="relative z-[1] flex flex-col items-center text-center px-4">
        <p className="mb-1 text-2xl opacity-60" aria-hidden>
          🌸 🌿
        </p>
        <h1 className="fun-display text-5xl font-bold tracking-tight" style={{ color: 'var(--garden-accent)' }}>
          MindShift Lab
        </h1>
        <p className="fun-display mt-2 max-w-md font-medium" style={{ color: 'var(--garden-text-soft)' }}>
          Grow videos like a garden — playful steps, serious output
        </p>
        <button
          type="button"
          onClick={() => setModalOpen(true)}
          className="fun-cta-pop fun-display mt-8 rounded-2xl px-8 py-3 text-lg font-bold shadow-md"
          style={{ backgroundColor: 'var(--garden-accent)', color: 'var(--garden-cream)' }}
        >
          Start New Video
        </button>
      </div>

      <button
        type="button"
        onClick={() => setDrawerOpen(true)}
        className="fixed right-4 top-4 z-[2] rounded-2xl border-2 bg-[var(--garden-card)] px-4 py-2 text-sm font-semibold shadow-sm transition-all hover:shadow-md"
        style={{ borderColor: 'var(--garden-accent)', color: 'var(--garden-accent)' }}
      >
        Previous Videos
      </button>

      {modalOpen && (
        <div
          className="fixed inset-0 z-40 flex items-center justify-center"
          style={{ backgroundColor: 'var(--garden-overlay)' }}
          onClick={() => setModalOpen(false)}
        >
          <div
            className="z-50 w-full max-w-md rounded-2xl border-2 p-6 shadow-xl"
            style={{
              backgroundColor: 'var(--garden-modal-bg)',
              borderColor: 'var(--garden-border-strong)',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="mb-1 text-lg font-semibold" style={{ color: 'var(--garden-text)' }}>
              New Video
            </h2>
            <p className="mb-4 text-sm" style={{ color: 'var(--garden-text-soft)' }}>
              Give your project a sunny name
            </p>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Title"
              className="mb-3 w-full rounded-xl border-2 px-3 py-2 outline-none focus:ring-2 focus:ring-[var(--garden-accent)]"
              style={{
                borderColor: 'var(--garden-border-strong)',
                backgroundColor: 'var(--garden-input)',
                color: 'var(--garden-text)',
              }}
            />
            <fieldset className="mb-4 text-left">
              <legend className="text-xs font-semibold mb-2" style={{ color: 'var(--garden-text-soft)' }}>
                Scene images &mdash; prompt style
              </legend>
              <label className="flex items-center gap-2 mb-1 cursor-pointer">
                <input
                  type="radio"
                  name="imgprov"
                  checked={imageProvider === 'grok'}
                  onChange={() => setImageProvider('grok')}
                />
                <span className="text-sm" style={{ color: 'var(--garden-text)' }}>
                  Grok (detailed text prompts &mdash; paste into Grok image)
                </span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="imgprov"
                  checked={imageProvider === 'midjourney'}
                  onChange={() => setImageProvider('midjourney')}
                />
                <span className="text-sm" style={{ color: 'var(--garden-text)' }}>
                  Midjourney (&mdash;sref / params at end of line)
                </span>
              </label>
            </fieldset>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={handleCreate}
                disabled={!title.trim()}
                className="rounded-xl px-4 py-2 font-semibold disabled:opacity-50"
                style={{ backgroundColor: 'var(--garden-accent)', color: 'var(--garden-cream)' }}
              >
                Create
              </button>
              <button
                type="button"
                onClick={() => {
                  setModalOpen(false);
                  setTitle('');
                }}
                className="rounded-xl border-2 px-4 py-2 font-medium"
                style={{
                  borderColor: 'var(--garden-border)',
                  color: 'var(--garden-text-soft)',
                  backgroundColor: 'var(--garden-surface)',
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      <div
        className="fixed right-0 top-0 z-30 h-full w-80 border-l-2 shadow-xl transition-transform duration-300 ease-out"
        style={{
          transform: drawerOpen ? 'translateX(0)' : 'translateX(100%)',
          backgroundColor: 'var(--garden-card)',
          borderColor: 'var(--garden-border-strong)',
        }}
      >
        <div
          className="flex items-center justify-between border-b-2 p-4"
          style={{ borderColor: 'var(--garden-border)' }}
        >
          <span className="font-semibold" style={{ color: 'var(--garden-text)' }}>
            Previous Videos
          </span>
          <button
            type="button"
            onClick={() => setDrawerOpen(false)}
            className="rounded-lg px-2 py-1 font-medium transition-colors hover:opacity-80"
            style={{ color: 'var(--garden-text-soft)' }}
            aria-label="Close"
          >
            X
          </button>
        </div>
        <div className="overflow-y-auto p-4">
          {projects.length === 0 ? (
            <p style={{ color: 'var(--garden-text-soft)' }}>No videos yet — plant your first one above</p>
          ) : (
            projects.map((project) => (
              <div
                key={project.id}
                role="button"
                tabIndex={0}
                onClick={() => {
                  setDrawerOpen(false);
                  router.push('/project/' + encodeProjectIdForRoute(project.id));
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    setDrawerOpen(false);
                    router.push('/project/' + encodeProjectIdForRoute(project.id));
                  }
                }}
                className="mb-2 cursor-pointer rounded-xl border-2 p-4 transition-all hover:shadow-md"
                style={{
                  backgroundColor: 'var(--garden-surface)',
                  borderColor: 'var(--garden-border)',
                }}
              >
                <div className="font-bold" style={{ color: 'var(--garden-text)' }}>
                  {project.title}
                </div>
                <span className="text-sm font-medium" style={{ color: 'var(--garden-accent)' }}>
                  Step {((project.current_step ?? 0) + 1)}/11
                </span>
                {project.mtime != null && (
                  <div className="mt-1 text-sm" style={{ color: 'var(--garden-text-soft)' }}>
                    {new Date(project.mtime * 1000).toLocaleDateString()}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>

      {toasts.map((t) => (
        <Toast
          key={t.id}
          id={t.id}
          message={t.message}
          type={t.type}
          onClose={() => removeToast(t.id)}
        />
      ))}
    </div>
  );
}
