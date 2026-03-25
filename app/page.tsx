'use client';

import { useRouter } from 'next/navigation';
import { useState, useEffect } from 'react';
import { listProjects, createProject } from '@/lib/api';
import useToast from '@/hooks/useToast';
import Toast from '@/components/Toast';

type Project = { id: string; title: string; current_step?: number; mtime?: number };

export default function Page() {
  const router = useRouter();
  const { toasts, showToast, removeToast } = useToast();
  const [modalOpen, setModalOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [title, setTitle] = useState('');
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
      const result = await createProject(t);
      setModalOpen(false);
      setTitle('');
      router.push('/project/' + result.id);
      showToast('Project created', 'success');
    } catch {
      showToast('Failed to create project', 'error');
    }
  }

  return (
    <div className="fixed inset-0 flex items-center justify-center bg-[#0a1210] overflow-y-auto">
      <div className="flex flex-col items-center text-center">
        <h1 className="text-5xl font-bold" style={{ color: '#3DBA6F' }}>
          MindShift Lab
        </h1>
        <p className="mt-2" style={{ color: '#8FAF96' }}>
          Your automated video production system
        </p>
        <button
          type="button"
          onClick={() => setModalOpen(true)}
          className="mt-8 rounded-lg px-6 py-3 text-lg font-medium text-white transition-opacity hover:opacity-90"
          style={{ backgroundColor: '#3DBA6F' }}
        >
          Start New Video
        </button>
      </div>

      <button
        type="button"
        onClick={() => setDrawerOpen(true)}
        className="fixed right-4 top-4 rounded border px-3 py-1.5 text-sm font-medium transition-opacity hover:opacity-90"
        style={{ borderColor: '#3DBA6F', color: '#3DBA6F' }}
      >
        Previous Videos
      </button>

      {modalOpen && (
        <div
          className="fixed inset-0 z-40 flex items-center justify-center bg-black/60"
          onClick={() => setModalOpen(false)}
        >
          <div
            className="z-50 w-full max-w-md rounded-xl bg-[#132419] p-6 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="mb-4 text-lg font-semibold text-white">New Video</h2>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Title"
              className="mb-4 w-full rounded border bg-[#0D1A12] px-3 py-2 text-white placeholder-[#8FAF96] outline-none"
              style={{ borderColor: '#3DBA6F' }}
            />
            <div className="flex gap-2">
              <button
                type="button"
                onClick={handleCreate}
                disabled={!title.trim()}
                className="rounded-lg px-4 py-2 font-medium text-white disabled:opacity-50"
                style={{ backgroundColor: '#3DBA6F' }}
              >
                Create
              </button>
              <button
                type="button"
                onClick={() => { setModalOpen(false); setTitle(''); }}
                className="rounded-lg border px-4 py-2 font-medium text-[#8FAF96]"
                style={{ borderColor: '#8FAF96' }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      <div
        className="fixed right-0 top-0 z-30 h-full w-80 bg-[#132419] shadow-xl transition-transform duration-300 ease-out"
        style={{ transform: drawerOpen ? 'translateX(0)' : 'translateX(100%)' }}
      >
        <div className="flex items-center justify-between border-b border-[#0D1A12] p-4">
          <span className="font-semibold text-white">Previous Videos</span>
          <button
            type="button"
            onClick={() => setDrawerOpen(false)}
            className="text-[#8FAF96] hover:text-white"
            aria-label="Close"
          >
            X
          </button>
        </div>
        <div className="overflow-y-auto p-4">
          {projects.length === 0 ? (
            <p className="text-[#8FAF96]">No videos yet</p>
          ) : (
            projects.map((project) => (
              <div
                key={project.id}
                role="button"
                tabIndex={0}
                onClick={() => {
                  setDrawerOpen(false);
                  router.push('/project/' + project.id);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    setDrawerOpen(false);
                    router.push('/project/' + project.id);
                  }
                }}
                className="mb-2 cursor-pointer rounded p-4 transition-opacity hover:opacity-90"
                style={{ backgroundColor: '#0D1A12' }}
              >
                <div className="font-bold text-white">{project.title}</div>
                <span className="text-sm" style={{ color: '#3DBA6F' }}>
                  Step {((project.current_step ?? 0) + 1)}/7
                </span>
                {project.mtime != null && (
                  <div className="mt-1 text-sm" style={{ color: '#8FAF96' }}>
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
