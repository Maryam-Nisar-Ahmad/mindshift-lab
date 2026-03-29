'use client';

import { useParams, useRouter } from 'next/navigation';
import { useState, useEffect, useCallback, useRef } from 'react';
import { getProject, resetStep, deleteProject, getPromptTemplate, updateTitle, stepComplete, cleanScript, saveScript, getPrompts, generatePrompts, getJobStatus, checkImages, regeneratePrompt, fileUrl, uploadAudio, getAudioStatus, assembleSegments, getSeoData, saveSeoData, type SeoDataPayload } from '@/lib/api';
import { projectIdFromParams, encodeProjectIdForRoute } from '@/lib/projectId';
import useToast from '@/hooks/useToast';
import Toast from '@/components/Toast';
import SceneBatchPage from '@/components/SceneBatchPage';

type Project = { id: string; title: string; current_step?: number; mtime?: number };
type ProjectWithReviewMeta = Project & { exportedVideoPath?: string; videoUrl?: string };

const STEPS = [
  { num: 1, name: 'Channel Strategy' },
  { num: 2, name: 'Script Generation' },
  { num: 3, name: 'MidJourney Prompts' },
  { num: 3.5, name: 'Image Check' },
  { num: 4, name: 'Thumbnail' },
  { num: 4.1, name: 'Thumbnail Review' },
  { num: 4.5, name: 'Script for Recording' },
  { num: 5, name: 'Audio Processing' },
  { num: 6, name: 'Assemble and Edit' },
  { num: 7, name: 'SEO Description' },
  { num: 8, name: 'Final Review' },
];

/** Per-step accent for left stripe + soft tint (cycles). */
const STEP_STRIPE_COLORS = [
  'var(--garden-petal-deep)',
  'var(--garden-lilac)',
  'var(--garden-blue)',
  'var(--garden-warning)',
  'var(--garden-leaf-stem)',
  'var(--garden-accent)',
  'var(--garden-missing)',
  'var(--garden-petal)',
  'var(--garden-amber)',
  'var(--garden-danger)',
  'var(--garden-border-strong)',
];

function StepStatusPill({
  viewingStep,
  num,
  currentStep,
}: {
  viewingStep: number | null;
  num: number;
  currentStep: number;
}) {
  if (viewingStep === num) {
    return (
      <span
        className="fun-display inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-bold"
        style={{
          backgroundColor: 'color-mix(in srgb, var(--garden-blue) 24%, white)',
          color: 'var(--garden-text)',
          border: '1px solid color-mix(in srgb, var(--garden-blue) 40%, transparent)',
        }}
      >
        <span aria-hidden>👀</span> Viewing
      </span>
    );
  }
  if (currentStep === num) {
    if (viewingStep != null) {
      return (
        <span
          className="fun-display inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-bold"
          style={{
            backgroundColor: 'color-mix(in srgb, var(--garden-warning) 35%, var(--garden-card))',
            color: 'var(--garden-text)',
          }}
        >
          <span aria-hidden>📍</span> Your progress
        </span>
      );
    }
    return (
      <span
        className="fun-display inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-bold shadow-sm"
        style={{
          background: 'linear-gradient(135deg, var(--garden-accent) 0%, var(--garden-leaf-stem) 100%)',
          color: 'var(--garden-cream)',
        }}
      >
        <span aria-hidden>✨</span> Active
      </span>
    );
  }
  if (currentStep > num) {
    return (
      <span
        className="fun-display inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-bold"
        style={{
          backgroundColor: 'color-mix(in srgb, var(--garden-accent) 16%, var(--garden-card))',
          color: 'var(--garden-accent-hover)',
          border: '1px solid color-mix(in srgb, var(--garden-accent) 28%, transparent)',
        }}
      >
        <span aria-hidden>✓</span> Done
      </span>
    );
  }
  const isNextPending = STEPS.find((s) => currentStep < s.num)?.num === num;
  return (
    <span
      className="fun-display inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold"
      style={{
        backgroundColor: 'color-mix(in srgb, var(--garden-step-muted) 12%, var(--garden-card))',
        color: 'var(--garden-text-soft)',
      }}
    >
      <span aria-hidden>{isNextPending ? '🌱' : '○'}</span> {isNextPending ? 'Next' : 'Later'}
    </span>
  );
}

function Step8FieldCopyButton({
  ariaLabel,
  copied,
  onCopy,
}: {
  ariaLabel: string;
  copied: boolean;
  onCopy: () => void;
}) {
  return (
    <button
      type="button"
      aria-label={ariaLabel}
      className="absolute top-2 right-2 z-10 rounded-md p-1.5 transition-colors hover:opacity-90"
      style={{ color: 'var(--garden-text-soft)' }}
      onClick={onCopy}
    >
      {copied ? (
        <span className="text-xs font-medium" style={{ color: 'var(--garden-accent)' }}>
          ✓
        </span>
      ) : (
        <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <rect width="14" height="14" x="8" y="8" rx="2" ry="2" />
          <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
        </svg>
      )}
    </button>
  );
}

export default function ProjectPage() {
  const params = useParams();
  const router = useRouter();
  const id = projectIdFromParams(params.id);
  const { toasts, showToast, removeToast } = useToast();

  const [project, setProject] = useState<Project | null>(null);
  const [projectError, setProjectError] = useState<string | null>(null);
  const [resetModal, setResetModal] = useState<{ open: true; step: number } | { open: false }>({ open: false });
  /** When set, user is browsing an earlier step without changing saved progress on the server. */
  const [viewingStep, setViewingStep] = useState<number | null>(null);
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const [deleteConfirmTitle, setDeleteConfirmTitle] = useState('');
  const [step1Template, setStep1Template] = useState<string | null>(null);
  const [step2Template, setStep2Template] = useState<string | null>(null);
  const [step1Title, setStep1Title] = useState('');
  const [step2Script, setStep2Script] = useState('');
  const [step1Copied, setStep1Copied] = useState(false);
  const [step2Copied, setStep2Copied] = useState(false);
  const [step3Prompts, setStep3Prompts] = useState<{ segment_id: number; segment_text: string; mj_line: string }[] | null>(null);
  const [step3JobId, setStep3JobId] = useState<string | null>(null);
  const [step3JobStatusText, setStep3JobStatusText] = useState('');
  const [imageCheckResult, setImageCheckResult] = useState<{ present: string[]; missing: string[]; total_expected: number } | null>(null);
  const [step4Template, setStep4Template] = useState<string | null>(null);
  const [step4CustomInput, setStep4CustomInput] = useState<string>('');
  const [step4Copied, setStep4Copied] = useState(false);
  const [step4_5Script, setStep4_5Script] = useState<string>('');
  const [step4_5Copied, setStep4_5Copied] = useState(false);
  const [step5Status, setStep5Status] = useState<'idle' | 'transcribing' | 'correcting' | 'done' | 'error'>('idle');
  const [step5Result, setStep5Result] = useState<{ duration_sec?: number; word_count?: number; words_corrected?: number } | null>(null);
  const [step5Error, setStep5Error] = useState<string | null>(null);
  const [step7Combined, setStep7Combined] = useState<string>('');
  const [step7Copied, setStep7Copied] = useState(false);
  const [thumbnailReviewPrompt, setThumbnailReviewPrompt] = useState('');
  const [seoYoutubeDescription, setSeoYoutubeDescription] = useState('');
  const [seoChapterTimestamps, setSeoChapterTimestamps] = useState('');
  const [seoTags, setSeoTags] = useState('');
  const [seoTitleField, setSeoTitleField] = useState('');
  const [step8CopiedField, setStep8CopiedField] = useState<string | null>(null);
  const step8CopyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [everActiveSteps, setEverActiveSteps] = useState<Record<string, boolean>>({});
  const [manuallyExpandedSteps, setManuallyExpandedSteps] = useState<Record<string, boolean>>({});
  const promptDoneSoundPlayedRef = useRef<string | null>(null);

  const copyStep8Field = useCallback(
    (fieldKey: string, text: string) => {
      const value = (text ?? '').trim();
      if (!value) {
        showToast('Nothing to copy', 'error');
        return;
      }
      navigator.clipboard
        .writeText(text ?? '')
        .then(() => {
          setStep8CopiedField(fieldKey);
          if (step8CopyTimeoutRef.current) clearTimeout(step8CopyTimeoutRef.current);
          step8CopyTimeoutRef.current = setTimeout(() => setStep8CopiedField(null), 2000);
        })
        .catch(() => showToast('Copy failed', 'error'));
    },
    [showToast]
  );

  const fetchProject = useCallback(() => {
    if (!id) {
      setProject(null);
      setProjectError('Invalid project link.');
      return;
    }
    setProjectError(null);
    getProject(id)
      .then((data) => {
        setProject(data);
        setProjectError(null);
      })
      .catch((err) => {
        setProject(null);
        const msg = err instanceof Error ? err.message : 'Request failed';
        setProjectError(msg);
        showToast(msg, 'error');
      });
  }, [id, showToast]);

  useEffect(() => {
    queueMicrotask(() => {
      fetchProject();
    });
  }, [fetchProject]);

  const currentStep = typeof project?.current_step === 'number' ? project.current_step : Number(project?.current_step ?? 0);
  const displayStep = viewingStep ?? currentStep;
  const heroStepIndex = STEPS.findIndex((s) => s.num === currentStep);
  useEffect(() => {
    const key = String(currentStep);
    if (!Number.isFinite(currentStep)) return;
    queueMicrotask(() => {
      setEverActiveSteps((prev) => (prev[key] ? prev : { ...prev, [key]: true }));
    });
  }, [currentStep]);
  useEffect(() => {
    if (viewingStep == null) return;
    const key = String(viewingStep);
    if (!Number.isFinite(viewingStep)) return;
    queueMicrotask(() => {
      setEverActiveSteps((prev) => (prev[key] ? prev : { ...prev, [key]: true }));
    });
  }, [viewingStep]);
  useEffect(() => {
    if (displayStep === 1) {
      getPromptTemplate('1_channel_strategy')
        .then((r: { content?: string }) => setStep1Template(r?.content ?? null))
        .catch((err) => {
          setStep1Template(null);
          showToast(err instanceof Error ? err.message : 'Request failed', 'error');
        });
    }
  }, [displayStep, showToast]);
  useEffect(() => {
    if (displayStep === 2) {
      getPromptTemplate('2_script_generation')
        .then((r: { content?: string }) => setStep2Template(r?.content ?? null))
        .catch((err) => {
          setStep2Template(null);
          showToast(err instanceof Error ? err.message : 'Request failed', 'error');
        });
    }
  }, [displayStep, showToast]);
  useEffect(() => {
    if (id && displayStep >= 3) {
      getPrompts(id)
        .then((p: { segment_id: number; segment_text: string; mj_line: string }[]) => setStep3Prompts(Array.isArray(p) ? p : null))
        .catch((err) => {
          setStep3Prompts(null);
          showToast(err instanceof Error ? err.message : 'Request failed', 'error');
        });
    }
  }, [id, displayStep, showToast]);
  const playPromptDoneSound = useCallback(() => {
    if (typeof window === 'undefined') return;
    const AudioCtx = window.AudioContext || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    const now = ctx.currentTime;
    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.08, now + 0.03);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.7);
    gain.connect(ctx.destination);

    const osc1 = ctx.createOscillator();
    osc1.type = 'sine';
    osc1.frequency.setValueAtTime(880, now);
    osc1.connect(gain);
    osc1.start(now);
    osc1.stop(now + 0.22);

    const osc2 = ctx.createOscillator();
    osc2.type = 'sine';
    osc2.frequency.setValueAtTime(1320, now + 0.24);
    osc2.connect(gain);
    osc2.start(now + 0.24);
    osc2.stop(now + 0.56);

    void ctx.resume().catch(() => {});
    setTimeout(() => {
      void ctx.close().catch(() => {});
    }, 900);
  }, []);
  useEffect(() => {
    if (id && displayStep === 4) {
      Promise.all([
        getPromptTemplate('4_thumbnail').then((r: { content?: string }) => r?.content ?? ''),
        fetch(fileUrl(id, 'script.txt'))
          .then((r) => r.text())
          .catch(() => ''),
      ])
        .then(([tpl, script]) => {
          const combined = (tpl + (project?.title ? '\n\n' + project.title : '') + (script ? '\n\n' + script : '')).trim();
          setStep4Template(combined);
        })
        .catch((err) => {
          setStep4Template(null);
          showToast(err instanceof Error ? err.message : 'Request failed', 'error');
        });
    }
  }, [id, displayStep, project?.title, showToast]);
  useEffect(() => {
    if (!id || viewingStep !== 2) return;
    fetch(fileUrl(id, 'script.txt'))
      .then((r) => r.text())
      .then((text) => setStep2Script(text))
      .catch(() => {});
  }, [id, viewingStep]);
  useEffect(() => {
    if (!id) return;
    const saved = localStorage.getItem(`step4CustomInput_${id}`);
    if (saved) {
      queueMicrotask(() => setStep4CustomInput(saved));
    }
  }, [id]);
  useEffect(() => {
    if (id && displayStep === 4.1) {
      getPromptTemplate('8_thumbnail_review_prompt')
        .then((r: { content?: string }) => setThumbnailReviewPrompt(r?.content ?? ''))
        .catch(() => {});
    }
  }, [id, displayStep]);
  useEffect(() => {
    if (!id || (displayStep !== 7 && displayStep !== 8)) return;
    getSeoData(id)
      .then((data: SeoDataPayload) => {
        setSeoYoutubeDescription(data.description ?? '');
        setSeoChapterTimestamps(data.chapter_timestamps ?? '');
        setSeoTags(data.tags ?? '');
        setSeoTitleField(data.seo_title ?? '');
      })
      .catch(() => {});
  }, [id, displayStep]);
  useEffect(() => {
    if (id && displayStep === 4.5) {
      fetch(fileUrl(id, 'script.txt'))
        .then((r) => r.text())
        .then(setStep4_5Script)
        .catch(() => {
          setStep4_5Script('');
          showToast('Failed to load script', 'error');
        });
    }
  }, [id, displayStep, showToast]);
  useEffect(() => {
    if (id && displayStep === 7) {
      Promise.all([
        getPromptTemplate('5_seo').then((r: { content?: string }) => r?.content ?? ''),
        fetch(fileUrl(id, 'script.txt')).then((r) => r.text()).catch(() => ''),
      ])
        .then(([tpl, script]) => {
          const combined = (tpl + (project?.title ? '\n\n' + project.title : '') + (script ? '\n\n' + script : '')).trim();
          setStep7Combined(combined);
        })
        .catch((err) => showToast(err instanceof Error ? err.message : 'Request failed', 'error'));
    }
  }, [id, displayStep, project?.title, showToast]);
  useEffect(() => {
    if (!id || displayStep !== 5) return;
    getAudioStatus(id)
      .then((s: { status?: string; duration_sec?: number; word_count?: number; words_corrected?: number; error?: string }) => {
        if (s?.status === 'transcribing') setStep5Status('transcribing');
        else if (s?.status === 'correcting') setStep5Status('correcting');
        else if (s?.status === 'done') {
          setStep5Status('done');
          setStep5Result({ duration_sec: s.duration_sec, word_count: s.word_count, words_corrected: s.words_corrected });
          setStep5Error(null);
        } else if (s?.status === 'error') {
          setStep5Status('error');
          setStep5Error(s?.error ?? 'Processing failed');
        } else {
          setStep5Status('idle');
          setStep5Result(null);
          setStep5Error(null);
        }
      })
      .catch(() => {});
  }, [id, displayStep]);
  useEffect(() => {
    if (!step3JobId) return;
    const interval = setInterval(() => {
      getJobStatus(step3JobId)
        .then((s: { status?: string }) => {
          if (s?.status === 'splitting') setStep3JobStatusText('Analyzing script...');
          else if (s?.status === 'generating') setStep3JobStatusText('Generating prompts...');
          if (s?.status === 'done') {
            if (promptDoneSoundPlayedRef.current !== step3JobId) {
              playPromptDoneSound();
              promptDoneSoundPlayedRef.current = step3JobId;
            }
            setStep3JobId(null);
            if (id) getPrompts(id).then((p: { segment_id: number; segment_text: string; mj_line: string }[]) => setStep3Prompts(Array.isArray(p) ? p : null)).catch(() => {});
          }
        })
        .catch(() => {});
    }, 2000);
    return () => clearInterval(interval);
  }, [step3JobId, id, playPromptDoneSound]);
  useEffect(() => {
    if (!id || (step5Status !== 'transcribing' && step5Status !== 'correcting')) return;
    const interval = setInterval(() => {
      getAudioStatus(id)
        .then((s: { status?: string; duration_sec?: number; word_count?: number; words_corrected?: number; error?: string }) => {
          if (s?.status === 'transcribing') setStep5Status('transcribing');
          else if (s?.status === 'correcting') setStep5Status('correcting');
          else if (s?.status === 'done') {
            setStep5Status('done');
            setStep5Result({ duration_sec: s.duration_sec, word_count: s.word_count, words_corrected: s.words_corrected });
          } else if (s?.status === 'error') {
            setStep5Status('error');
            setStep5Error(s?.error ?? 'Processing failed');
          }
        })
        .catch(() => {});
    }, 3000);
    return () => clearInterval(interval);
  }, [id, step5Status]);
  function handleUpload(file: File) {
    if (!id) return;
    if (!file.name.toLowerCase().endsWith('.mp3')) {
      showToast('Please upload an .mp3 file', 'error');
      return;
    }
    uploadAudio(id, file)
      .then(() => {
        setStep5Error(null);
        setStep5Result(null);
        getAudioStatus(id)
          .then((s: { status?: string; duration_sec?: number; word_count?: number; words_corrected?: number; error?: string }) => {
            if (s?.status === 'transcribing') setStep5Status('transcribing');
            else if (s?.status === 'correcting') setStep5Status('correcting');
            else if (s?.status === 'done') {
              setStep5Status('done');
              setStep5Result({ duration_sec: s.duration_sec, word_count: s.word_count, words_corrected: s.words_corrected });
            } else if (s?.status === 'error') {
              setStep5Status('error');
              setStep5Error(s?.error ?? 'Processing failed');
            } else setStep5Status('transcribing');
          })
          .catch((err) => {
            setStep5Status('transcribing');
            showToast(err instanceof Error ? err.message : 'Request failed', 'error');
          });
      })
      .catch((err) => showToast(err instanceof Error ? err.message : 'Upload failed', 'error'));
  }

  function handleBackClick() {
    router.push('/');
  }

  function handleGoBack(step: number) {
    setResetModal({ open: true, step });
  }

  function stepPanelElementId(step: number) {
    return step === 7 ? 'step-7' : `step-panel-${String(step).replace(/\./g, '-')}`;
  }

  function handleViewStepOnly() {
    if (!resetModal.open || !('step' in resetModal)) return;
    const step = resetModal.step;
    if (step === 1 && project?.title != null) {
      setStep1Title(project.title);
    }
    setViewingStep(step);
    const key = String(step);
    setManuallyExpandedSteps((prev) => ({ ...prev, [key]: true }));
    setResetModal({ open: false });
    queueMicrotask(() => {
      document.getElementById(stepPanelElementId(step))?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }

  function handleConfirmReset() {
    if (!resetModal.open || !id) return;
    const step = 'step' in resetModal ? resetModal.step : 0;
    resetStep(id, step)
      .then(() => {
        setResetModal({ open: false });
        setViewingStep(null);
        fetchProject();
        showToast('Step reset', 'success');
      })
      .catch((err) => showToast(err instanceof Error ? err.message : 'Failed to reset step', 'error'));
  }

  function handleResetVideoClick() {
    setDeleteModalOpen(true);
  }

  function handleDeleteConfirm() {
    if (!id || deleteConfirmTitle !== project?.title) return;
    deleteProject(id, deleteConfirmTitle)
      .then(() => {
        setDeleteModalOpen(false);
        setDeleteConfirmTitle('');
        showToast('Project deleted', 'success');
        router.push('/');
      })
      .catch((err) => showToast(err instanceof Error ? err.message : 'Failed to delete project', 'error'));
  }

  if (!project) {
    return (
      <>
        <header
          className="fun-app-header sticky top-0 z-10 flex h-14 items-center px-6"
          style={{ backgroundColor: 'color-mix(in srgb, var(--garden-card) 82%, transparent)' }}
        >
          <button type="button" onClick={handleBackClick} className="mr-3 hover:opacity-75"
          style={{ color: 'var(--garden-text-soft)' }} aria-label="Back">
            ←
          </button>
          <span className="fun-display text-lg font-bold tracking-tight" style={{ color: 'var(--garden-accent)' }}>
            MindShift Lab <span className="fun-brand-leaf" aria-hidden>🌿</span>
          </span>
        </header>
        <div className="min-h-screen overflow-y-auto px-6 py-8" style={{ backgroundColor: 'var(--garden-surface)' }}>
          {projectError ? (
            <div className="max-w-md space-y-4">
              <p style={{ color: 'var(--garden-danger)' }}>{projectError}</p>
              <p className="text-sm" style={{ color: 'var(--garden-text-soft)' }}>
                Check that the backend is running and the project folder still exists under videos/.
              </p>
              <button
                type="button"
                className="rounded px-3 py-1.5 text-sm font-medium"
                style={{ backgroundColor: 'var(--garden-accent)', color: 'var(--garden-cream)' }}
                onClick={handleBackClick}
              >
                Back to home
              </button>
            </div>
          ) : (
            <p style={{ color: 'var(--garden-text-soft)' }}>Loading…</p>
          )}
        </div>
        {toasts.length > 0 && (
          <div className="fixed top-4 right-4 z-50 flex flex-col gap-2">
            {toasts.map((t) => (
              <Toast key={t.id} id={t.id} message={t.message} type={t.type} onClose={() => removeToast(t.id)} />
            ))}
          </div>
        )}
      </>
    );
  }

  return (
    <>
      <header
        className="fun-app-header sticky top-0 z-10 flex h-14 items-center px-6"
        style={{ backgroundColor: 'color-mix(in srgb, var(--garden-card) 82%, transparent)' }}
      >
        <button type="button" onClick={handleBackClick} className="mr-3 hover:opacity-75"
          style={{ color: 'var(--garden-text-soft)' }} aria-label="Back">
          ←
        </button>
        <span className="fun-display text-lg font-bold tracking-tight" style={{ color: 'var(--garden-accent)' }}>
          MindShift Lab <span className="fun-brand-leaf" aria-hidden>🌿</span>
        </span>
      </header>

      {viewingStep != null && Number.isFinite(currentStep) && (
        <div
          className="sticky top-14 z-10 mx-6 mt-2 flex flex-col gap-2 rounded-2xl px-4 py-3 sm:flex-row sm:items-center sm:justify-between shadow-md"
          style={{
            backgroundColor: 'var(--garden-banner)',
            border: '2px solid var(--garden-amber)',
          }}
        >
          <p className="fun-display text-sm font-medium" style={{ color: 'var(--garden-text)' }}>
            Viewing step{' '}
            <span className="font-semibold" style={{ color: 'var(--garden-amber)' }}>
              {viewingStep}
            </span>
            {' — '}
            your saved progress remains step{' '}
            <span className="font-semibold" style={{ color: 'var(--garden-accent)' }}>
              {currentStep}
            </span>
            .
          </p>
          <button
            type="button"
            className="fun-display shrink-0 rounded-xl px-3 py-1.5 text-sm font-bold transition-transform hover:scale-[1.02]"
            style={{ backgroundColor: 'var(--garden-accent)', color: 'var(--garden-cream)' }}
            onClick={() => setViewingStep(null)}
          >
            Return to current step
          </button>
        </div>
      )}

      {(project?.current_step ?? 0) > 8 && (
        <div
          className="fun-display sticky top-14 z-10 mx-6 mt-2 flex justify-between items-center rounded-2xl border-2 p-4 font-bold shadow-lg"
          style={{
            backgroundColor: 'var(--garden-accent)',
            color: 'var(--garden-cream)',
            borderColor: 'var(--garden-border-strong)',
          }}
        >
          <span>🎬 Video complete — grab your file!</span>
          <a href={fileUrl(id, 'output.mp4')} download className="underline font-medium hover:no-underline">
            Download
          </a>
        </div>
      )}

      <main className="min-h-screen overflow-y-auto px-6 py-8 max-w-3xl mx-auto" style={{ backgroundColor: 'var(--garden-surface)' }}>
        <div className="mb-8">
          <h1
            className="fun-display text-2xl sm:text-3xl font-bold leading-tight tracking-tight"
            style={{ color: 'var(--garden-text)', textShadow: '0 1px 0 var(--garden-leaf-soft)' }}
          >
            {project.title}
          </h1>
          <p className="fun-display mt-3 text-sm sm:text-base font-medium" style={{ color: 'var(--garden-text-soft)' }}>
            {(project?.current_step ?? 0) > 8
              ? '🎉 All steps complete — celebrate your video!'
              : heroStepIndex >= 0
                ? `You're on step ${heroStepIndex + 1} of ${STEPS.length} — little wins add up!`
                : '🌱 Pick up where you left off — your steps are waiting.'}
          </p>
        </div>

        {STEPS.map(({ num, name }) => {
          const complete = currentStep > num;
          const active = viewingStep === num || (viewingStep == null && currentStep === num);
          const key = String(num);
          const expanded = active || (!!everActiveSteps[key] && !!manuallyExpandedSteps[key]);
          const stepIdx = STEPS.findIndex((s) => s.num === num);
          const stripe = STEP_STRIPE_COLORS[stepIdx % STEP_STRIPE_COLORS.length];

          return (
            <div
              key={num}
              id={num === 7 ? 'step-7' : `step-panel-${String(num).replace(/\./g, '-')}`}
              className={`fun-step-card p-5 mb-4 ${active ? 'fun-step-card-active' : ''}`}
              style={{
                backgroundImage: `linear-gradient(145deg, var(--garden-card) 0%, color-mix(in srgb, ${stripe} 16%, var(--garden-card)) 100%)`,
                border: active ? '2px solid var(--garden-accent)' : '2px solid color-mix(in srgb, var(--garden-border) 55%, transparent)',
                borderLeft: `5px solid ${stripe}`,
              }}
            >
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <div className="flex items-center gap-3 min-w-0">
                  <span
                    className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-sm font-bold transition-all ${active ? 'fun-step-badge-pulse' : ''}`}
                    style={{
                      backgroundColor:
                        currentStep >= num || viewingStep === num ? 'var(--garden-accent)' : 'var(--garden-step-muted)',
                      color: 'var(--garden-cream)',
                    }}
                  >
                    {num}
                  </span>
                  <button
                    type="button"
                    className="fun-display font-bold text-left hover:opacity-90 text-base sm:text-lg min-w-0"
                    style={{ color: 'var(--garden-text)' }}
                    onClick={() => {
                      if (!active && !complete && !everActiveSteps[key]) return;
                      setManuallyExpandedSteps((prev) => ({ ...prev, [key]: !prev[key] }));
                    }}
                  >
                    {name}
                  </button>
                </div>
                <StepStatusPill viewingStep={viewingStep} num={num} currentStep={currentStep} />
              </div>

              {expanded && (
                <div className="mt-4">
                  {num === 1 && (
                    <>
                      <div className="rounded-lg p-4 border-l-4 max-h-64 overflow-y-auto relative font-mono text-sm" style={{ backgroundColor: 'var(--garden-surface)', borderLeftColor: 'var(--garden-accent)', color: 'var(--garden-text)' }}>
                        <button
                          type="button"
                          className="absolute top-2 right-2 rounded px-2 py-1 text-xs"
                          style={{ backgroundColor: 'var(--garden-card)', color: 'var(--garden-text)' }}
                          onClick={() => {
                            if (step1Template) {
                              navigator.clipboard.writeText(step1Template);
                              setStep1Copied(true);
                              setTimeout(() => setStep1Copied(false), 2000);
                            }
                          }}
                        >
                          {step1Copied ? '✓' : 'Copy'}
                        </button>
                        <pre className="whitespace-pre-wrap pr-16">{step1Template ?? 'Loading…'}</pre>
                      </div>
                      <input
                        type="text"
                        value={step1Title}
                        onChange={(e) => setStep1Title(e.target.value)}
                        placeholder="Title"
                        className="garden-input-field mt-3 w-full rounded-xl px-3 py-2 placeholder:opacity-50"
                      />
                      <button
                        type="button"
                        className="mt-2 rounded px-3 py-1.5 text-sm font-medium"
                        style={{ backgroundColor: 'var(--garden-accent)', color: 'var(--garden-cream)' }}
                          onClick={() => {
                            if (!id) return;
                            updateTitle(id, step1Title)
                              .then(() => stepComplete(id, 1))
                              .then(() => { fetchProject(); showToast('Saved', 'success'); })
                              .catch((err) => showToast(err instanceof Error ? err.message : 'Failed to save', 'error'));
                          }}
                      >
                        Save
                      </button>
                    </>
                  )}
                  {num === 2 && (
                    <>
                      <div className="rounded-lg p-4 border-l-4 max-h-64 overflow-y-auto relative font-mono text-sm" style={{ backgroundColor: 'var(--garden-surface)', borderLeftColor: 'var(--garden-accent)', color: 'var(--garden-text)' }}>
                        <button
                          type="button"
                          className="absolute top-2 right-2 rounded px-2 py-1 text-xs"
                          style={{ backgroundColor: 'var(--garden-card)', color: 'var(--garden-text)' }}
                          onClick={() => {
                            const text = ((step2Template ?? '') + (project?.title ? '\n\n' + project.title : '')).trim();
                            if (text) {
                              navigator.clipboard.writeText(text);
                              setStep2Copied(true);
                              setTimeout(() => setStep2Copied(false), 2000);
                            }
                          }}
                        >
                          {step2Copied ? '✓' : 'Copy'}
                        </button>
                        <pre className="whitespace-pre-wrap pr-16">{(step2Template ?? 'Loading…') + (project?.title ? '\n\n' + project.title : '')}</pre>
                      </div>
                      <textarea
                        value={step2Script}
                        onChange={(e) => setStep2Script(e.target.value)}
                        placeholder="Paste your script from Claude here..."
                        className="mt-3 w-full h-64 rounded font-mono text-sm resize-y border focus:border-[color:var(--garden-accent)] focus:outline-none"
                        style={{ backgroundColor: 'var(--garden-surface)', borderColor: 'var(--garden-border)' }}
                      />
                      <div className="mt-2 flex gap-2">
                        <button
                          type="button"
                          className="rounded-xl border-2 px-3 py-1.5 text-sm font-medium transition-colors hover:opacity-90"
                          style={{ borderColor: 'var(--garden-amber)', color: 'var(--garden-text)' }}
                          onClick={() => {
                            if (!id || !step2Script.trim()) return;
                            cleanScript(id, step2Script)
                              .then((r: { cleaned_script?: string }) => { setStep2Script(r?.cleaned_script ?? step2Script); showToast('Script cleaned', 'success'); })
                              .catch((err) => showToast(err instanceof Error ? err.message : 'Clean failed', 'error'));
                          }}
                        >
                          Clean with AI
                        </button>
                        <button
                          type="button"
                          className="rounded px-3 py-1.5 text-sm font-medium"
                          style={{ backgroundColor: 'var(--garden-accent)', color: 'var(--garden-cream)' }}
                          onClick={() => {
                            if (!id) return;
                            saveScript(id, step2Script, true)
                              .then(() => stepComplete(id, 2))
                              .then(() => { fetchProject(); showToast('Script saved', 'success'); })
                              .catch((err) => showToast(err instanceof Error ? err.message : 'Failed to save script', 'error'));
                          }}
                        >
                          Save Script
                        </button>
                      </div>
                    </>
                  )}
                  {num === 3 && (
                    <>
                      {step3JobId ? (
                        <div className="mt-4 flex items-center gap-2" style={{ color: 'var(--garden-text-soft)' }}>
                          <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-[color:var(--garden-accent)] border-t-transparent" />
                          {step3JobStatusText || 'Processing...'}
                        </div>
                      ) : step3Prompts && step3Prompts.length > 0 ? (
                        <div className="mt-4">
                          <SceneBatchPage
                            prompts={step3Prompts}
                            projectId={id}
                            onRegenerate={async (segId, segText) => {
                              const r = await regeneratePrompt(id, String(segId), segText);
                              if (id) getPrompts(id).then((p: { segment_id: number; segment_text: string; mj_line: string }[]) => setStep3Prompts(Array.isArray(p) ? p : null));
                              return r as { mj_line: string };
                            }}
                          />
                        </div>
                      ) : (
                        <button
                          type="button"
                          className="mt-4 rounded px-3 py-1.5 text-sm font-medium"
                          style={{ backgroundColor: 'var(--garden-accent)', color: 'var(--garden-cream)' }}
                          onClick={() => {
                            if (!id) return;
                            generatePrompts(id)
                              .then((r: { job_id?: string }) => {
                                if (r?.job_id) {
                                  setStep3JobId(r.job_id);
                                  setStep3JobStatusText('Analyzing script...');
                                }
                              })
                              .catch((err) => showToast(err instanceof Error ? err.message : 'Request failed', 'error'));
                          }}
                        >
                          Generate All Prompts
                        </button>
                      )}
                    </>
                  )}
                  {num === 3.5 && (
                    <>
                      <p className="mt-4 text-sm" style={{ color: 'var(--garden-text-soft)' }}>
                        Save images as 1.png 2.png... into images/ folder.
                      </p>
                      <button
                        type="button"
                        className="mt-2 rounded-xl border-2 px-3 py-1.5 text-sm font-medium transition-colors hover:opacity-90"
                        style={{ borderColor: 'var(--garden-border-strong)', color: 'var(--garden-text)' }}
                        onClick={() => {
                          if (!id) return;
                          checkImages(id)
                            .then((r: { present?: string[]; missing?: string[]; total_expected?: number }) => setImageCheckResult({ present: r?.present ?? [], missing: r?.missing ?? [], total_expected: r?.total_expected ?? 0 }))
                            .catch((err) => {
                              setImageCheckResult(null);
                              showToast(err instanceof Error ? err.message : 'Request failed', 'error');
                            });
                        }}
                      >
                        Check Images
                      </button>
                      {imageCheckResult && (
                        <div className="mt-3 flex flex-wrap gap-2">
                          {(imageCheckResult.present ?? []).map((f) => (
                            <span key={f} className="rounded-full px-2 py-0.5 text-xs font-medium" style={{ backgroundColor: 'var(--garden-accent)', color: 'var(--garden-cream)' }}>{f}</span>
                          ))}
                          {(imageCheckResult.missing ?? []).map((f) => (
                            <span
                              key={f}
                              className="rounded-full px-2 py-0.5 text-xs font-medium"
                              style={{ backgroundColor: 'var(--garden-danger)', color: 'var(--garden-cream)' }}
                            >
                              {f}
                            </span>
                          ))}
                        </div>
                      )}
                      {imageCheckResult && imageCheckResult.total_expected > 0 && imageCheckResult.missing?.length === 0 && (
                        <button
                          type="button"
                          className="mt-3 rounded px-3 py-1.5 text-sm font-medium"
                          style={{ backgroundColor: 'var(--garden-accent)', color: 'var(--garden-cream)' }}
                          onClick={() => {
                            if (!id) return;
                            stepComplete(id, 3.5)
                              .then(() => { fetchProject(); showToast('Step 3.5 complete', 'success'); })
                              .catch((err) => showToast(err instanceof Error ? err.message : 'Failed', 'error'));
                          }}
                        >
                          Mark complete
                        </button>
                      )}
                    </>
                  )}
                  {num === 4 && (
                    <>
                      <div className="mt-4 rounded-lg p-4 border-l-4 max-h-64 overflow-y-auto relative font-mono text-sm" style={{ backgroundColor: 'var(--garden-surface)', borderLeftColor: 'var(--garden-accent)', color: 'var(--garden-text)' }}>
                        <button
                          type="button"
                          className="absolute top-2 right-2 rounded px-2 py-1 text-xs"
                          style={{ backgroundColor: 'var(--garden-card)', color: 'var(--garden-text)' }}
                          onClick={() => {
                            const text = (step4Template ?? '').trim() + (step4CustomInput.trim() ? "\n\n" + step4CustomInput.trim() : "");
                            if (text) {
                              navigator.clipboard.writeText(text);
                              setStep4Copied(true);
                              setTimeout(() => setStep4Copied(false), 2000);
                            }
                          }}
                        >
                          {step4Copied ? '✓' : 'Copy'}
                        </button>
                        <pre className="whitespace-pre-wrap pr-16">{step4Template ?? 'Loading…'}</pre>
                      </div>
                      <p className="mt-3 text-sm font-medium" style={{ color: 'var(--garden-text)' }}>
                        Add custom notes (optional)
                      </p>
                      <input
                        type="text"
                        value={step4CustomInput}
                        onChange={(e) => {
                          setStep4CustomInput(e.target.value);
                          localStorage.setItem(`step4CustomInput_${id}`, e.target.value);
                        }}
                        placeholder="e.g. add specific instructions..."
                        className="garden-input-field mt-3 w-full rounded-xl px-3 py-2 placeholder:opacity-50"
                      />
                      <button
                        type="button"
                        className="mt-3 rounded px-3 py-1.5 text-sm font-medium"
                        style={{ backgroundColor: 'var(--garden-accent)', color: 'var(--garden-cream)' }}
                        onClick={() => {
                          if (!id) return;
                          stepComplete(id, 4, 4.1)
                            .then(() => fetchProject())
                            .then(() => showToast('Step complete', 'success'))
                            .catch((err) => showToast(err instanceof Error ? err.message : 'Failed', 'error'));
                        }}
                      >
                        Mark Thumbnail Done
                      </button>
                    </>
                  )}
                  {num === 4.1 && (
                    <>
                      <h2 className="mt-4 text-lg font-semibold" style={{ color: 'var(--garden-text)' }}>
                        Thumbnail Review
                      </h2>
                      <div className="mt-3 rounded-lg p-4 border-l-4 max-h-64 overflow-y-auto font-mono text-sm" style={{ backgroundColor: 'var(--garden-surface)', borderLeftColor: 'var(--garden-accent)', color: 'var(--garden-text)' }}>
                        <pre className="whitespace-pre-wrap">{thumbnailReviewPrompt || 'Loading…'}</pre>
                      </div>
                      <button
                        type="button"
                        className="mt-2 rounded px-2 py-1 text-xs"
                        style={{ backgroundColor: 'var(--garden-card)', color: 'var(--garden-text)' }}
                        onClick={() => {
                          const extra = step4CustomInput.trim();
                          const text = thumbnailReviewPrompt + (extra ? '\n\n' + extra : '');
                          navigator.clipboard.writeText(text);
                        }}
                      >
                        Copy Prompt
                      </button>
                      <button
                        type="button"
                        className="mt-3 rounded px-3 py-1.5 text-sm font-medium"
                        style={{ backgroundColor: 'var(--garden-accent)', color: 'var(--garden-cream)' }}
                        onClick={() => {
                          if (!id) return;
                          stepComplete(id, 4.1, 4.5)
                            .then(() => fetchProject())
                            .then(() => showToast('Thumbnail review complete', 'success'))
                            .catch((err) => showToast(err instanceof Error ? err.message : 'Failed', 'error'));
                        }}
                      >
                        Mark Complete
                      </button>
                    </>
                  )}
                  {num === 4.5 && (
                    <>
                      <p className="mt-4 text-sm font-medium" style={{ color: 'var(--garden-accent)' }}>Your Recording Script</p>
                      <div className="mt-2 rounded-lg p-4 border-l-4 font-mono text-sm max-h-80 overflow-y-auto relative" style={{ backgroundColor: 'var(--garden-surface)', borderLeftColor: 'var(--garden-accent)', color: 'var(--garden-text)' }}>
                        <button
                          type="button"
                          className="absolute top-2 right-2 rounded px-2 py-1 text-xs"
                          style={{ backgroundColor: 'var(--garden-card)', color: 'var(--garden-text)' }}
                          onClick={() => {
                            if (!step4_5Script) return;
                            navigator.clipboard.writeText(step4_5Script);
                            setStep4_5Copied(true);
                            setTimeout(() => setStep4_5Copied(false), 1500);
                          }}
                        >
                          {step4_5Copied ? '✓' : '📋'}
                        </button>
                        <pre className="whitespace-pre-wrap pr-16">{step4_5Script || 'Loading…'}</pre>
                      </div>
                      <p className="mt-4" style={{ color: 'var(--garden-text-soft)' }}>
                        Save your audio file here:
                      </p>
                      <p className="mt-1 font-mono text-sm" style={{ color: 'var(--garden-accent)' }}>MindShift-Lab/videos/{id}/audio.mp3</p>
                      <button
                        type="button"
                        className="mt-3 rounded px-3 py-1.5 text-sm font-medium"
                        style={{ backgroundColor: 'var(--garden-accent)', color: 'var(--garden-cream)' }}
                        onClick={() => {
                          if (!id) return;
                          stepComplete(id, 4.5, 5)
                            .then(() => fetchProject())
                            .then(() => showToast('Step complete', 'success'))
                            .catch((err) => showToast(err instanceof Error ? err.message : 'Failed', 'error'));
                        }}
                      >
                        Proceed to Upload
                      </button>
                    </>
                  )}
                  {num === 5 && (
                    <>
                      <div
                        className="mt-4 rounded-xl p-8 text-center border-2 border-dashed"
                        style={{ backgroundColor: 'var(--garden-timeline)', borderColor: 'var(--garden-border)' }}
                        onDrop={(e) => {
                          e.preventDefault();
                          const file = e.dataTransfer.files[0];
                          if (file) handleUpload(file);
                        }}
                        onDragOver={(e) => e.preventDefault()}
                      >
                        <input
                          type="file"
                          accept=".mp3"
                          className="hidden"
                          id="step5-file"
                          onChange={(e) => {
                            const file = e.target.files?.[0];
                            if (file) handleUpload(file);
                            e.target.value = '';
                          }}
                        />
                        {step5Status === 'idle' && (
                          <label
                            htmlFor="step5-file"
                            className="cursor-pointer hover:opacity-80"
                            style={{ color: 'var(--garden-text-soft)' }}
                          >
                            Drop .mp3 here or click to browse
                          </label>
                        )}
                        {step5Status === 'transcribing' && (
                          <div className="flex items-center justify-center gap-2" style={{ color: 'var(--garden-text-soft)' }}>
                            <span className="inline-block h-5 w-5 animate-spin rounded-full border-2 border-blue-500 border-t-transparent" />
                            transcribing
                          </div>
                        )}
                        {step5Status === 'correcting' && (
                          <div className="flex items-center justify-center gap-2" style={{ color: 'var(--garden-text-soft)' }}>
                            <span className="inline-block h-5 w-5 animate-spin rounded-full border-2 border-blue-500 border-t-transparent" />
                            correcting
                          </div>
                        )}
                        {step5Status === 'done' && step5Result && (
                          <div className="rounded-xl p-4 text-left border border-[color:var(--garden-accent)]" style={{ backgroundColor: 'var(--garden-surface)' }}>
                            <p className="font-mono text-sm" style={{ color: 'var(--garden-text)' }}>
                              duration {Math.floor((step5Result.duration_sec ?? 0) / 60)}:{(String((step5Result.duration_sec ?? 0) % 60)).padStart(2, '0')}
                            </p>
                            <p className="text-sm" style={{ color: 'var(--garden-text-soft)' }}>
                              word count {step5Result.word_count ?? 0}
                            </p>
                            <p className="text-sm" style={{ color: 'var(--garden-text-soft)' }}>
                              words corrected {step5Result.words_corrected ?? 0}
                            </p>
                            <button
                              type="button"
                              className="mt-3 rounded px-3 py-1.5 text-sm font-medium"
                              style={{ backgroundColor: 'var(--garden-accent)', color: 'var(--garden-cream)' }}
                              onClick={() => {
                                assembleSegments(id)
                                  .then(() => router.push('/project/' + encodeProjectIdForRoute(id) + '/editor'))
                                  .catch((err) => showToast(err instanceof Error ? err.message : 'Assembly failed', 'error'));
                              }}
                            >
                              Proceed to Assembly
                            </button>
                          </div>
                        )}
                        {step5Status === 'error' && (
                          <div
                            className="rounded-xl border-2 p-4 text-left text-sm"
                            style={{
                              backgroundColor: 'var(--garden-danger-soft)',
                              borderColor: 'var(--garden-danger)',
                              color: 'var(--garden-text)',
                            }}
                          >
                            {step5Error ?? 'Error'}
                          </div>
                        )}
                      </div>
                    </>
                  )}
                  {num === 6 && (
                    <>
                      <p className="mt-4 text-sm leading-relaxed" style={{ color: 'var(--garden-text-soft)' }}>
                        The timeline, segments, subtitles, and export tools live in the{' '}
                        <span className="font-semibold" style={{ color: 'var(--garden-text)' }}>
                          assembly editor
                        </span>
                        . Open it anytime to change your edit — your saved project step stays where it is until you finish
                        later steps.
                      </p>
                      <button
                        type="button"
                        className="mt-4 rounded-xl px-4 py-2 text-sm font-semibold shadow-sm transition-all hover:brightness-105 w-full sm:w-auto"
                        style={{ backgroundColor: 'var(--garden-accent)', color: 'var(--garden-cream)' }}
                        onClick={() => {
                          if (!id) return;
                          router.push('/project/' + encodeProjectIdForRoute(id) + '/editor');
                        }}
                      >
                        Open assembly editor
                      </button>
                    </>
                  )}
                  {num === 7 && (
                    <>
                      <div className="mt-4 rounded-lg p-4 border-l-4 max-h-64 overflow-y-auto relative font-mono text-sm" style={{ backgroundColor: 'var(--garden-surface)', borderLeftColor: 'var(--garden-accent)', color: 'var(--garden-text)' }}>
                        <button
                          type="button"
                          className="absolute top-2 right-2 rounded px-2 py-1 text-xs"
                          style={{ backgroundColor: 'var(--garden-card)', color: 'var(--garden-text)' }}
                          onClick={() => {
                            if (step7Combined) {
                              navigator.clipboard.writeText(step7Combined);
                              setStep7Copied(true);
                              setTimeout(() => setStep7Copied(false), 2000);
                            }
                          }}
                        >
                          {step7Copied ? '✓' : 'Copy'}
                        </button>
                        <pre className="whitespace-pre-wrap pr-16">{step7Combined || 'Loading…'}</pre>
                      </div>
                      <div className="mt-4 space-y-4">
                        <div>
                          <label className="garden-label-soft block text-sm mb-1">YouTube description</label>
                          <textarea
                            value={seoYoutubeDescription}
                            onChange={(e) => setSeoYoutubeDescription(e.target.value)}
                            className="w-full min-h-[120px] rounded font-mono text-sm resize-y border focus:border-[color:var(--garden-accent)] focus:outline-none p-3"
                            style={{ backgroundColor: 'var(--garden-surface)', borderColor: 'var(--garden-border)', color: 'var(--garden-text)' }}
                          />
                        </div>
                        <div>
                          <label className="garden-label-soft block text-sm mb-1">Chapter timestamps</label>
                          <textarea
                            value={seoChapterTimestamps}
                            onChange={(e) => setSeoChapterTimestamps(e.target.value)}
                            className="w-full min-h-[100px] rounded font-mono text-sm resize-y border focus:border-[color:var(--garden-accent)] focus:outline-none p-3"
                            style={{ backgroundColor: 'var(--garden-surface)', borderColor: 'var(--garden-border)', color: 'var(--garden-text)' }}
                          />
                        </div>
                        <div>
                          <label className="garden-label-soft block text-sm mb-1">Tags</label>
                          <textarea
                            value={seoTags}
                            onChange={(e) => setSeoTags(e.target.value)}
                            placeholder="Comma-separated or one per line"
                            className="w-full min-h-[80px] rounded font-mono text-sm resize-y border focus:border-[color:var(--garden-accent)] focus:outline-none p-3"
                            style={{ backgroundColor: 'var(--garden-surface)', borderColor: 'var(--garden-border)', color: 'var(--garden-text)' }}
                          />
                        </div>
                        <div>
                          <label className="garden-label-soft block text-sm mb-1">SEO title</label>
                          <input
                            type="text"
                            value={seoTitleField}
                            onChange={(e) => setSeoTitleField(e.target.value)}
                            className="w-full rounded font-mono text-sm border focus:border-[color:var(--garden-accent)] focus:outline-none p-3"
                            style={{ backgroundColor: 'var(--garden-surface)', borderColor: 'var(--garden-border)', color: 'var(--garden-text)' }}
                          />
                        </div>
                      </div>
                      <div className="mt-3">
                        <button
                          type="button"
                          className="rounded px-3 py-1.5 text-sm font-medium"
                          style={{ backgroundColor: 'var(--garden-accent)', color: 'var(--garden-cream)' }}
                          onClick={() => {
                            if (!id) return;
                            const payload: SeoDataPayload = {
                              description: seoYoutubeDescription,
                              chapter_timestamps: seoChapterTimestamps,
                              tags: seoTags,
                              seo_title: seoTitleField,
                              cleaned_script: '',
                            };
                            saveSeoData(id, payload)
                              .then(() => stepComplete(id, 7))
                              .then(() => {
                                fetchProject();
                                showToast('Saved', 'success');
                              })
                              .catch((err) => showToast(err instanceof Error ? err.message : 'Failed to save', 'error'));
                          }}
                        >
                          Save
                        </button>
                      </div>
                    </>
                  )}
                  {num === 8 && (
                    <>
                      {/* Final Review - Step 8 */}
                      <div className="space-y-6">
                        <h3 className="text-lg font-semibold" style={{ color: 'var(--garden-text)' }}>
                          Final Review
                        </h3>

                        <div>
                          <label className="garden-label-soft block text-sm mb-1">Video Title</label>
                          <pre className="garden-pre p-4 rounded whitespace-pre-wrap">{project?.title ?? '—'}</pre>
                        </div>

                        <div>
                          <label className="garden-label-soft block text-sm mb-1">YouTube description</label>
                          <div className="relative">
                            <Step8FieldCopyButton
                              ariaLabel="Copy YouTube description"
                              copied={step8CopiedField === 'seo-youtube'}
                              onCopy={() => copyStep8Field('seo-youtube', seoYoutubeDescription)}
                            />
                            <textarea
                              value={seoYoutubeDescription}
                              onChange={(e) => setSeoYoutubeDescription(e.target.value)}
                              className="garden-input-field w-full min-h-[120px] py-3 pl-4 pr-12 rounded whitespace-pre-wrap"
                            />
                          </div>
                        </div>

                        <div>
                          <label className="garden-label-soft block text-sm mb-1">Chapter timestamps</label>
                          <div className="relative">
                            <Step8FieldCopyButton
                              ariaLabel="Copy chapter timestamps"
                              copied={step8CopiedField === 'seo-chapters'}
                              onCopy={() => copyStep8Field('seo-chapters', seoChapterTimestamps)}
                            />
                            <textarea
                              value={seoChapterTimestamps}
                              onChange={(e) => setSeoChapterTimestamps(e.target.value)}
                              className="garden-input-field w-full min-h-[100px] py-3 pl-4 pr-12 rounded whitespace-pre-wrap"
                            />
                          </div>
                        </div>

                        <div>
                          <label className="garden-label-soft block text-sm mb-1">Tags</label>
                          <div className="relative">
                            <Step8FieldCopyButton
                              ariaLabel="Copy tags"
                              copied={step8CopiedField === 'seo-tags'}
                              onCopy={() => copyStep8Field('seo-tags', seoTags)}
                            />
                            <textarea
                              value={seoTags}
                              onChange={(e) => setSeoTags(e.target.value)}
                              className="garden-input-field w-full min-h-[80px] py-3 pl-4 pr-12 rounded whitespace-pre-wrap"
                            />
                          </div>
                        </div>

                        <div>
                          <label className="garden-label-soft block text-sm mb-1">SEO title</label>
                          <div className="relative">
                            <Step8FieldCopyButton
                              ariaLabel="Copy SEO title"
                              copied={step8CopiedField === 'seo-title'}
                              onCopy={() => copyStep8Field('seo-title', seoTitleField)}
                            />
                            <input
                              type="text"
                              value={seoTitleField}
                              onChange={(e) => setSeoTitleField(e.target.value)}
                              className="garden-input-field w-full py-3 pl-4 pr-12 rounded"
                            />
                          </div>
                        </div>

                        <div className="pt-4 border-t" style={{ borderColor: 'var(--garden-border)' }}>
                          <label className="garden-label-soft block text-sm mb-1">Exported Video File Path</label>
                          <div
                            className="garden-pre p-4 rounded font-mono text-sm break-all"
                            style={{ color: 'var(--garden-accent)' }}
                          >
                            {(project as ProjectWithReviewMeta)?.exportedVideoPath || (project as ProjectWithReviewMeta)?.videoUrl || 'Video not exported yet. Click "Export Video" in previous step.'}
                          </div>
                        </div>

                        <button
                          type="button"
                          onClick={() => {
                            if (!id) return;
                            const payload: SeoDataPayload = {
                              description: seoYoutubeDescription,
                              chapter_timestamps: seoChapterTimestamps,
                              tags: seoTags,
                              seo_title: seoTitleField,
                              cleaned_script: '',
                            };
                            saveSeoData(id, payload)
                              .then(() => stepComplete(id, 8))
                              .then(() => {
                                fetchProject();
                                showToast('Final review saved!', 'success');
                              })
                              .catch((err) => showToast(err instanceof Error ? err.message : 'Failed', 'error'));
                          }}
                          className="w-full py-3 rounded-xl font-semibold shadow-sm transition-all hover:brightness-105"
                          style={{ backgroundColor: 'var(--garden-accent)', color: 'var(--garden-cream)' }}
                        >
                          Save &amp; Complete Project
                        </button>
                      </div>
                    </>
                  )}
                  {num !== 1 && num !== 2 && num !== 3 && num !== 3.5 && num !== 4 && num !== 4.1 && num !== 4.5 && num !== 5 && num !== 6 && num !== 7 && num !== 8 && <div className="h-24 rounded-xl border border-dashed" style={{ borderColor: 'var(--garden-border)', backgroundColor: 'var(--garden-well)' }} />}
                </div>
              )}

              {complete && (
                <div className="mt-3">
                  <button
                    type="button"
                    onClick={() => handleGoBack(num)}
                    className="fun-display text-sm font-semibold underline decoration-2 underline-offset-4 transition-colors hover:opacity-90"
                    style={{ color: 'var(--garden-accent-hover)', textDecorationColor: 'color-mix(in srgb, var(--garden-accent) 55%, transparent)' }}
                  >
                    Go Back
                  </button>
                </div>
              )}
            </div>
          );
        })}

        <div className="mt-8">
          <button
            type="button"
            onClick={handleResetVideoClick}
            className="text-sm hover:opacity-90"
            style={{ color: 'var(--garden-danger)' }}
          >
            Reset Video
          </button>
        </div>
      </main>

      {resetModal.open && 'step' in resetModal && (
        <div
          className="fixed inset-0 z-40 flex items-center justify-center"
          style={{ backgroundColor: 'var(--garden-overlay)' }}
          onClick={() => setResetModal({ open: false })}
        >
          <div
            className="z-50 w-full max-w-sm rounded-2xl border-2 p-6 shadow-xl"
            style={{ backgroundColor: 'var(--garden-card)', borderColor: 'var(--garden-border-strong)' }}
            onClick={(e) => e.stopPropagation()}
          >
            <p className="mb-1 font-semibold" style={{ color: 'var(--garden-text)' }}>
              Open this step again?
            </p>
            <p className="text-sm mb-4 leading-relaxed" style={{ color: 'var(--garden-text-soft)' }}>
              <span className="font-medium" style={{ color: 'var(--garden-text)' }}>
                View only
              </span>{' '}
              — read prompts and copy content; your saved step and files stay as they are.
              <br />
              <span className="font-medium" style={{ color: 'var(--garden-text)' }}>
                Reset from here
              </span>{' '}
              — delete work from this step onward so you can redo it (same as before).
            </p>
            <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">
              <button
                type="button"
                onClick={() => setResetModal({ open: false })}
                className="rounded-xl px-3 py-1.5 text-sm sm:order-1 border-2"
                style={{ borderColor: 'var(--garden-border)', color: 'var(--garden-text-soft)' }}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleViewStepOnly}
                className="rounded-xl px-3 py-1.5 text-sm font-semibold sm:order-2"
                style={{ backgroundColor: 'var(--garden-blue)', color: 'var(--garden-cream)' }}
              >
                View only
              </button>
              <button
                type="button"
                onClick={handleConfirmReset}
                className="rounded-xl px-3 py-1.5 text-sm font-semibold sm:order-3"
                style={{ backgroundColor: 'var(--garden-accent)', color: 'var(--garden-cream)' }}
              >
                Reset from here
              </button>
            </div>
          </div>
        </div>
      )}

      {deleteModalOpen && (
        <div
          className="fixed inset-0 z-40 flex items-center justify-center"
          style={{ backgroundColor: 'var(--garden-overlay)' }}
          onClick={() => setDeleteModalOpen(false)}
        >
          <div
            className="z-50 w-full max-w-md rounded-2xl border-2 p-6 shadow-xl"
            style={{ backgroundColor: 'var(--garden-card)', borderColor: 'var(--garden-border-strong)' }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-lg font-semibold mb-2" style={{ color: 'var(--garden-text)' }}>
              Reset Video
            </h2>
            <p className="text-sm mb-4" style={{ color: 'var(--garden-text-soft)' }}>
              Type the project title to confirm deletion.
            </p>
            <input
              type="text"
              value={deleteConfirmTitle}
              onChange={(e) => setDeleteConfirmTitle(e.target.value)}
              placeholder={project.title}
              className="garden-input-field mb-4 w-full rounded-xl px-3 py-2"
            />
            <div className="flex gap-2 justify-end">
              <button
                type="button"
                onClick={() => setDeleteModalOpen(false)}
                className="rounded-xl border-2 px-3 py-1.5 text-sm"
                style={{ borderColor: 'var(--garden-border)', color: 'var(--garden-text-soft)' }}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleDeleteConfirm}
                disabled={deleteConfirmTitle !== project.title}
                className="rounded-xl px-3 py-1.5 text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed"
                style={{
                  backgroundColor:
                    deleteConfirmTitle === project.title ? 'var(--garden-danger)' : 'var(--garden-step-muted)',
                  color: 'var(--garden-cream)',
                }}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

      {toasts.length > 0 && (
        <div className="fixed top-4 right-4 z-50 flex flex-col gap-2">
          {toasts.map((t) => (
            <Toast key={t.id} id={t.id} message={t.message} type={t.type} onClose={() => removeToast(t.id)} />
          ))}
        </div>
      )}
    </>
  );
}
