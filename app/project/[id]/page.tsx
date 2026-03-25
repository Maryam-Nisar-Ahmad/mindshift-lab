'use client';

import { useParams, useRouter } from 'next/navigation';
import { useState, useEffect, useCallback } from 'react';
import { getProject, resetStep, deleteProject, getPromptTemplate, updateTitle, stepComplete, cleanScript, saveScript, getPrompts, generatePrompts, getJobStatus, checkImages, regeneratePrompt, fileUrl, uploadAudio, getAudioStatus, assembleSegments, getSeoData, saveSeoData } from '@/lib/api';
import useToast from '@/hooks/useToast';
import Toast from '@/components/Toast';
import SceneBatchPage from '@/components/SceneBatchPage';

type Project = { id: string; title: string; current_step?: number; mtime?: number };

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

export default function ProjectPage() {
  const params = useParams();
  const router = useRouter();
  const id = typeof params.id === 'string' ? params.id : '';
  const { toasts, showToast, removeToast } = useToast();

  const [project, setProject] = useState<Project | null>(null);
  const [resetModal, setResetModal] = useState<{ open: true; step: number } | { open: false }>({ open: false });
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
  const [step7Template, setStep7Template] = useState<string | null>(null);
  const [step7ScriptText, setStep7ScriptText] = useState<string>('');
  const [step7Combined, setStep7Combined] = useState<string>('');
  const [step7OutputText, setStep7OutputText] = useState('');
  const [step7Copied, setStep7Copied] = useState(false);
  const [thumbnailReviewPrompt, setThumbnailReviewPrompt] = useState('');
  const [step8Description, setStep8Description] = useState('');
  const [step8Tags, setStep8Tags] = useState('');
  const [everActiveSteps, setEverActiveSteps] = useState<Record<string, boolean>>({});
  const [manuallyExpandedSteps, setManuallyExpandedSteps] = useState<Record<string, boolean>>({});

  const fetchProject = useCallback(() => {
    if (!id) return;
    getProject(id)
      .then((data) => {
        setProject(data);
        console.log('project data:', data, 'current_step:', data?.current_step, 'type:', typeof data?.current_step);
      })
      .catch((err) => {
        setProject(null);
        showToast(err instanceof Error ? err.message : 'Request failed', 'error');
      });
  }, [id, showToast]);

  useEffect(() => {
    fetchProject();
  }, [fetchProject]);

  const currentStep = typeof project?.current_step === 'number' ? project.current_step : Number(project?.current_step ?? 0);
  useEffect(() => {
    const key = String(currentStep);
    if (!Number.isFinite(currentStep)) return;
    setEverActiveSteps((prev) => (prev[key] ? prev : { ...prev, [key]: true }));
  }, [currentStep]);
  useEffect(() => {
    if (currentStep === 1) {
      getPromptTemplate('1_channel_strategy')
        .then((r: { content?: string }) => setStep1Template(r?.content ?? null))
        .catch((err) => {
          setStep1Template(null);
          showToast(err instanceof Error ? err.message : 'Request failed', 'error');
        });
    }
  }, [currentStep, showToast]);
  useEffect(() => {
    if (currentStep === 2) {
      getPromptTemplate('2_script_generation')
        .then((r: { content?: string }) => setStep2Template(r?.content ?? null))
        .catch((err) => {
          setStep2Template(null);
          showToast(err instanceof Error ? err.message : 'Request failed', 'error');
        });
    }
  }, [currentStep, showToast]);
  useEffect(() => {
    if (id && currentStep === 3) {
      getPrompts(id)
        .then((p: { segment_id: number; segment_text: string; mj_line: string }[]) => setStep3Prompts(Array.isArray(p) ? p : null))
        .catch((err) => {
          setStep3Prompts(null);
          showToast(err instanceof Error ? err.message : 'Request failed', 'error');
        });
    }
  }, [id, currentStep, showToast]);
  useEffect(() => {
    if (id && currentStep === 4) {
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
  }, [id, currentStep, project?.title, showToast]);
  useEffect(() => {
    if (!id) return;
    const saved = localStorage.getItem(`step4CustomInput_${id}`);
    if (saved) setStep4CustomInput(saved);
  }, [id]);
  useEffect(() => {
    if (id && currentStep === 4.1) {
      getPromptTemplate('8_thumbnail_review_prompt')
        .then((r: { content?: string }) => setThumbnailReviewPrompt(r?.content ?? ''))
        .catch(() => {});
    }
  }, [id, currentStep]);
  useEffect(() => {
    if (id && currentStep === 8) {
      getSeoData(id)
        .then((data) => {
          setStep8Description(data.description ?? '');
          setStep8Tags(data.tags ?? '');
        })
        .catch(() => {});
    }
  }, [id, currentStep]);
  useEffect(() => {
    if (id && currentStep === 4.5) {
      fetch(fileUrl(id, 'script.txt'))
        .then((r) => r.text())
        .then(setStep4_5Script)
        .catch(() => {
          setStep4_5Script('');
          showToast('Failed to load script', 'error');
        });
    }
  }, [id, currentStep, showToast]);
  useEffect(() => {
    if (id && currentStep === 7) {
      Promise.all([
        getPromptTemplate('5_seo').then((r: { content?: string }) => r?.content ?? ''),
        fetch(fileUrl(id, 'script.txt')).then((r) => r.text()).catch(() => ''),
      ])
        .then(([tpl, script]) => {
          setStep7Template(tpl);
          setStep7ScriptText(script);
          const combined = (tpl + (project?.title ? '\n\n' + project.title : '') + (script ? '\n\n' + script : '')).trim();
          setStep7Combined(combined);
          setStep7OutputText('');
        })
        .catch((err) => showToast(err instanceof Error ? err.message : 'Request failed', 'error'));
    }
  }, [id, currentStep, project?.title, showToast]);
  useEffect(() => {
    if (!step3JobId) return;
    const interval = setInterval(() => {
      getJobStatus(step3JobId)
        .then((s: { status?: string }) => {
          if (s?.status === 'splitting') setStep3JobStatusText('Analyzing script...');
          else if (s?.status === 'generating') setStep3JobStatusText('Generating prompts...');
          if (s?.status === 'done') {
            setStep3JobId(null);
            if (id) getPrompts(id).then((p: { segment_id: number; segment_text: string; mj_line: string }[]) => setStep3Prompts(Array.isArray(p) ? p : null)).catch(() => {});
          }
        })
        .catch(() => {});
    }, 2000);
    return () => clearInterval(interval);
  }, [step3JobId, id]);
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

  function handleConfirmReset() {
    if (!resetModal.open || resetModal.open === false || !id) return;
    const step = 'step' in resetModal ? resetModal.step : 0;
    resetStep(id, step)
      .then(() => {
        setResetModal({ open: false });
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
        <header className="sticky top-0 z-10 flex h-14 items-center px-6" style={{ backgroundColor: '#132419' }}>
          <button type="button" onClick={handleBackClick} className="mr-3 text-white/80 hover:text-white" aria-label="Back">
            ←
          </button>
          <span style={{ color: '#3DBA6F' }}>MindShift Lab</span>
        </header>
        <div className="min-h-screen overflow-y-auto px-6 py-8" style={{ backgroundColor: '#0D1A12' }}>
          <p className="text-white/70">Loading…</p>
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
      <header className="sticky top-0 z-10 flex h-14 items-center px-6" style={{ backgroundColor: '#132419' }}>
        <button type="button" onClick={handleBackClick} className="mr-3 text-white/80 hover:text-white" aria-label="Back">
          ←
        </button>
        <span style={{ color: '#3DBA6F' }}>MindShift Lab</span>
      </header>

      {(project?.current_step ?? 0) > 8 && (
        <div className="sticky top-14 z-10 mx-6 mt-2 flex justify-between items-center rounded-xl p-4 text-white" style={{ backgroundColor: '#3DBA6F' }}>
          <span>Video Complete! Ready to download.</span>
          <a href={fileUrl(id, 'output.mp4')} download className="underline font-medium hover:no-underline">Download</a>
        </div>
      )}

      <main className="min-h-screen overflow-y-auto px-6 py-8 max-w-3xl mx-auto" style={{ backgroundColor: '#0D1A12' }}>
        <h1 className="text-xl font-semibold text-white mb-8">{project.title}</h1>

        {STEPS.map(({ num, name }) => {
          const complete = currentStep > num;
          const active = currentStep === num;
          const pending = currentStep < num;
          const key = String(num);
          const expanded = active || (!!everActiveSteps[key] && !!manuallyExpandedSteps[key]);

          return (
            <div
              key={num}
              id={num === 7 ? 'step-7' : undefined}
              className="rounded-xl p-5 mb-4"
              style={{
                backgroundColor: '#132419',
                border: active ? '1px solid #3DBA6F' : undefined,
              }}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-medium text-white"
                    style={{
                      backgroundColor: active || complete ? '#3DBA6F' : '#6B7280',
                    }}
                  >
                    {num}
                  </span>
                  <button
                    type="button"
                    className="font-bold text-white text-left hover:opacity-90"
                    onClick={() => {
                      if (!active && !everActiveSteps[key]) return;
                      setManuallyExpandedSteps((prev) => ({ ...prev, [key]: !prev[key] }));
                    }}
                  >
                    {name}
                  </button>
                </div>
                <span className="text-sm text-white/70">
                  {active && 'Active'}
                  {complete && 'Completed'}
                  {pending && 'Pending'}
                </span>
              </div>

              {expanded && (
                <div className="mt-4">
                  {num === 1 && (
                    <>
                      <div className="rounded-lg p-4 border-l-4 max-h-64 overflow-y-auto relative font-mono text-sm" style={{ backgroundColor: '#0D1A12', borderLeftColor: '#3DBA6F', color: '#F0F4F0' }}>
                        <button
                          type="button"
                          className="absolute top-2 right-2 rounded px-2 py-1 text-xs"
                          style={{ backgroundColor: '#132419', color: '#F0F4F0' }}
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
                        className="mt-3 w-full rounded-lg border border-white/20 bg-white/5 px-3 py-2 text-white placeholder:text-white/40"
                      />
                      <button
                        type="button"
                        className="mt-2 rounded px-3 py-1.5 text-sm font-medium text-white"
                        style={{ backgroundColor: '#3DBA6F' }}
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
                      <div className="rounded-lg p-4 border-l-4 max-h-64 overflow-y-auto relative font-mono text-sm" style={{ backgroundColor: '#0D1A12', borderLeftColor: '#3DBA6F', color: '#F0F4F0' }}>
                        <button
                          type="button"
                          className="absolute top-2 right-2 rounded px-2 py-1 text-xs"
                          style={{ backgroundColor: '#132419', color: '#F0F4F0' }}
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
                        className="mt-3 w-full h-64 rounded font-mono text-sm resize-y border focus:border-[#3DBA6F] focus:outline-none"
                        style={{ backgroundColor: '#0D1A12', borderColor: '#2A4A35' }}
                      />
                      <div className="mt-2 flex gap-2">
                        <button
                          type="button"
                          className="rounded px-3 py-1.5 text-sm font-medium border border-amber-500 text-amber-500 hover:bg-amber-500/10"
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
                          className="rounded px-3 py-1.5 text-sm font-medium text-white"
                          style={{ backgroundColor: '#3DBA6F' }}
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
                        <div className="mt-4 flex items-center gap-2 text-white/80">
                          <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-[#3DBA6F] border-t-transparent" />
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
                          className="mt-4 rounded px-3 py-1.5 text-sm font-medium text-white"
                          style={{ backgroundColor: '#3DBA6F' }}
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
                      <p className="mt-4 text-white/80 text-sm">Save images as 1.png 2.png... into images/ folder.</p>
                      <button
                        type="button"
                        className="mt-2 rounded px-3 py-1.5 text-sm font-medium border border-white/30 text-white/90 hover:bg-white/10"
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
                            <span key={f} className="rounded-full px-2 py-0.5 text-xs font-medium text-white" style={{ backgroundColor: '#3DBA6F' }}>{f}</span>
                          ))}
                          {(imageCheckResult.missing ?? []).map((f) => (
                            <span key={f} className="rounded-full px-2 py-0.5 text-xs font-medium text-white bg-red-600">{f}</span>
                          ))}
                        </div>
                      )}
                      {imageCheckResult && imageCheckResult.total_expected > 0 && imageCheckResult.missing?.length === 0 && (
                        <button
                          type="button"
                          className="mt-3 rounded px-3 py-1.5 text-sm font-medium text-white"
                          style={{ backgroundColor: '#3DBA6F' }}
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
                      <div className="mt-4 rounded-lg p-4 border-l-4 max-h-64 overflow-y-auto relative font-mono text-sm" style={{ backgroundColor: '#0D1A12', borderLeftColor: '#3DBA6F', color: '#F0F4F0' }}>
                        <button
                          type="button"
                          className="absolute top-2 right-2 rounded px-2 py-1 text-xs"
                          style={{ backgroundColor: '#132419', color: '#F0F4F0' }}
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
                      <p className="mt-3 text-sm font-medium text-white/90">Add custom notes (optional)</p>
                      <input
                        type="text"
                        value={step4CustomInput}
                        onChange={(e) => {
                          setStep4CustomInput(e.target.value);
                          localStorage.setItem(`step4CustomInput_${id}`, e.target.value);
                        }}
                        placeholder="e.g. add specific instructions..."
                        className="mt-3 w-full rounded-lg border border-white/20 bg-white/5 px-3 py-2 text-white placeholder:text-white/40"
                      />
                      <button
                        type="button"
                        className="mt-3 rounded px-3 py-1.5 text-sm font-medium text-white"
                        style={{ backgroundColor: '#3DBA6F' }}
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
                      <h2 className="mt-4 text-lg font-semibold text-white">Thumbnail Review</h2>
                      <div className="mt-3 rounded-lg p-4 border-l-4 max-h-64 overflow-y-auto font-mono text-sm" style={{ backgroundColor: '#0D1A12', borderLeftColor: '#3DBA6F', color: '#F0F4F0' }}>
                        <pre className="whitespace-pre-wrap">{thumbnailReviewPrompt || 'Loading…'}</pre>
                      </div>
                      <button
                        type="button"
                        className="mt-2 rounded px-2 py-1 text-xs"
                        style={{ backgroundColor: '#132419', color: '#F0F4F0' }}
                        onClick={() => {
                          console.log('COPY DEBUG — step4CustomInput:', step4CustomInput, '| localStorage value:', localStorage.getItem(`step4CustomInput_${id}`));
                          const extra = step4CustomInput.trim();
                          const text = thumbnailReviewPrompt + (extra ? '\n\n' + extra : '');
                          navigator.clipboard.writeText(text);
                        }}
                      >
                        Copy Prompt
                      </button>
                      <button
                        type="button"
                        className="mt-3 rounded px-3 py-1.5 text-sm font-medium text-white"
                        style={{ backgroundColor: '#3DBA6F' }}
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
                      <p className="mt-4 text-sm font-medium" style={{ color: '#3DBA6F' }}>Your Recording Script</p>
                      <div className="mt-2 rounded-lg p-4 border-l-4 font-mono text-sm max-h-80 overflow-y-auto relative" style={{ backgroundColor: '#0D1A12', borderLeftColor: '#3DBA6F', color: '#F0F4F0' }}>
                        <button
                          type="button"
                          className="absolute top-2 right-2 rounded px-2 py-1 text-xs"
                          style={{ backgroundColor: '#132419', color: '#F0F4F0' }}
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
                      <p className="mt-4 text-white/80">Save your audio file here:</p>
                      <p className="mt-1 font-mono text-sm" style={{ color: '#3DBA6F' }}>MindShift-Lab/videos/{id}/audio.mp3</p>
                      <button
                        type="button"
                        className="mt-3 rounded px-3 py-1.5 text-sm font-medium text-white"
                        style={{ backgroundColor: '#3DBA6F' }}
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
                        style={{ backgroundColor: '#0A1510', borderColor: '#2A4A35' }}
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
                          <label htmlFor="step5-file" className="cursor-pointer text-white/80 hover:text-white">
                            Drop .mp3 here or click to browse
                          </label>
                        )}
                        {step5Status === 'transcribing' && (
                          <div className="flex items-center justify-center gap-2 text-white/80">
                            <span className="inline-block h-5 w-5 animate-spin rounded-full border-2 border-blue-500 border-t-transparent" />
                            transcribing
                          </div>
                        )}
                        {step5Status === 'correcting' && (
                          <div className="flex items-center justify-center gap-2 text-white/80">
                            <span className="inline-block h-5 w-5 animate-spin rounded-full border-2 border-blue-500 border-t-transparent" />
                            correcting
                          </div>
                        )}
                        {step5Status === 'done' && step5Result && (
                          <div className="rounded-xl p-4 text-left border border-[#3DBA6F]" style={{ backgroundColor: '#0D1A12' }}>
                            <p className="text-white font-mono text-sm">
                              duration {Math.floor((step5Result.duration_sec ?? 0) / 60)}:{(String((step5Result.duration_sec ?? 0) % 60)).padStart(2, '0')}
                            </p>
                            <p className="text-white/80 text-sm">word count {step5Result.word_count ?? 0}</p>
                            <p className="text-white/80 text-sm">words corrected {step5Result.words_corrected ?? 0}</p>
                            <button
                              type="button"
                              className="mt-3 rounded px-3 py-1.5 text-sm font-medium text-white"
                              style={{ backgroundColor: '#3DBA6F' }}
                              onClick={() => {
                                assembleSegments(id)
                                  .then(() => router.push('/project/' + id + '/editor'))
                                  .catch((err) => showToast(err instanceof Error ? err.message : 'Assembly failed', 'error'));
                              }}
                            >
                              Proceed to Assembly
                            </button>
                          </div>
                        )}
                        {step5Status === 'error' && (
                          <div className="rounded-xl p-4 text-left bg-red-900/30 border border-red-600 text-red-200 text-sm">
                            {step5Error ?? 'Error'}
                          </div>
                        )}
                      </div>
                    </>
                  )}
                  {num === 7 && (
                    <>
                      <div className="mt-4 rounded-lg p-4 border-l-4 max-h-64 overflow-y-auto relative font-mono text-sm" style={{ backgroundColor: '#0D1A12', borderLeftColor: '#3DBA6F', color: '#F0F4F0' }}>
                        <button
                          type="button"
                          className="absolute top-2 right-2 rounded px-2 py-1 text-xs"
                          style={{ backgroundColor: '#132419', color: '#F0F4F0' }}
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
                      <textarea
                        value={step7OutputText}
                        onChange={(e) => setStep7OutputText(e.target.value)}
                        placeholder="Paste your SEO result here"
                        className="mt-3 w-full h-40 rounded font-mono text-sm resize-y border focus:border-[#3DBA6F] focus:outline-none"
                        style={{ backgroundColor: '#0D1A12', borderColor: '#2A4A35', color: '#F0F4F0' }}
                      />
                      <div className="mt-2 flex gap-2">
                        <button
                          type="button"
                          className="rounded px-3 py-1.5 text-sm font-medium border border-amber-500 text-amber-500 hover:bg-amber-500/10"
                          onClick={() => {
                            if (!id || !step7OutputText.trim()) return;
                            cleanScript(id, step7OutputText)
                              .then((r: { cleaned_script?: string }) => { setStep7OutputText(r?.cleaned_script ?? step7OutputText); showToast('Script cleaned', 'success'); })
                              .catch((err) => showToast(err instanceof Error ? err.message : 'Clean failed', 'error'));
                          }}
                        >
                          Clean with AI
                        </button>
                        <button
                          type="button"
                          className="rounded px-3 py-1.5 text-sm font-medium text-white"
                          style={{ backgroundColor: '#3DBA6F' }}
                          onClick={() => {
                            if (!id) return;
                            saveScript(id, step7OutputText, true)
                              .then(() => stepComplete(id, 7))
                              .then(() => { fetchProject(); showToast('Saved', 'success'); })
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
                      <h2 className="mt-4 text-lg font-semibold text-white">Final Review</h2>
                      <p className="mt-3 text-sm font-medium text-white/90">Video Title</p>
                      <div className="mt-1 rounded-lg p-4 border-l-4 max-h-32 overflow-y-auto font-mono text-sm" style={{ backgroundColor: '#0D1A12', borderLeftColor: '#3DBA6F', color: '#F0F4F0' }}>
                        <pre className="whitespace-pre-wrap">{project?.title ?? '—'}</pre>
                      </div>
                      <p className="mt-4 text-sm font-medium text-white/90">SEO Description</p>
                      <textarea
                        value={step8Description}
                        onChange={(e) => setStep8Description(e.target.value)}
                        placeholder="Paste your SEO description here"
                        className="mt-1 w-full h-40 rounded font-mono text-sm resize-y border focus:border-[#3DBA6F] focus:outline-none"
                        style={{ backgroundColor: '#0D1A12', borderColor: '#2A4A35', color: '#F0F4F0' }}
                      />
                      <p className="mt-3 text-sm font-medium text-white/90">Tags</p>
                      <input
                        type="text"
                        value={step8Tags}
                        onChange={(e) => setStep8Tags(e.target.value)}
                        placeholder="tag1, tag2, tag3..."
                        className="mt-1 w-full rounded-lg border border-white/20 bg-white/5 px-3 py-2 text-white placeholder:text-white/40"
                      />
                      <button
                        type="button"
                        className="mt-3 rounded px-3 py-1.5 text-sm font-medium text-white"
                        style={{ backgroundColor: '#3DBA6F' }}
                        onClick={() => {
                          if (!id) return;
                          saveSeoData(id, { description: step8Description, tags: step8Tags })
                            .then(() => stepComplete(id, 8))
                            .then(() => {
                              fetchProject();
                              showToast('Final review saved!', 'success');
                            })
                            .catch((err) => showToast(err instanceof Error ? err.message : 'Failed', 'error'));
                        }}
                      >
                        Save &amp; Complete
                      </button>
                    </>
                  )}
                  {num !== 1 && num !== 2 && num !== 3 && num !== 3.5 && num !== 4 && num !== 4.1 && num !== 4.5 && num !== 5 && num !== 7 && num !== 8 && <div className="h-24 rounded-lg bg-white/5" />}
                </div>
              )}

              {complete && (
                <div className="mt-3">
                  <button
                    type="button"
                    onClick={() => handleGoBack(num)}
                    className="text-sm text-gray-400 hover:text-gray-300"
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
            className="text-sm text-red-500 hover:text-red-400"
          >
            Reset Video
          </button>
        </div>
      </main>

      {resetModal.open && 'step' in resetModal && (
        <div
          className="fixed inset-0 z-40 flex items-center justify-center bg-black/60"
          onClick={() => setResetModal({ open: false })}
        >
          <div
            className="z-50 w-full max-w-sm rounded-xl p-6 shadow-xl"
            style={{ backgroundColor: '#132419' }}
            onClick={(e) => e.stopPropagation()}
          >
            <p className="text-white mb-4">Go back to this step and reset progress after it?</p>
            <div className="flex gap-2 justify-end">
              <button
                type="button"
                onClick={() => setResetModal({ open: false })}
                className="rounded px-3 py-1.5 text-sm text-white/80 hover:text-white"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmReset}
                className="rounded px-3 py-1.5 text-sm font-medium text-white"
                style={{ backgroundColor: '#3DBA6F' }}
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}

      {deleteModalOpen && (
        <div
          className="fixed inset-0 z-40 flex items-center justify-center bg-black/60"
          onClick={() => setDeleteModalOpen(false)}
        >
          <div
            className="z-50 w-full max-w-md rounded-xl p-6 shadow-xl"
            style={{ backgroundColor: '#132419' }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-lg font-semibold text-white mb-2">Reset Video</h2>
            <p className="text-white/70 text-sm mb-4">Type the project title to confirm deletion.</p>
            <input
              type="text"
              value={deleteConfirmTitle}
              onChange={(e) => setDeleteConfirmTitle(e.target.value)}
              placeholder={project.title}
              className="w-full rounded-lg border border-white/20 bg-white/5 px-3 py-2 text-white placeholder:text-white/40 mb-4"
            />
            <div className="flex gap-2 justify-end">
              <button
                type="button"
                onClick={() => setDeleteModalOpen(false)}
                className="rounded px-3 py-1.5 text-sm text-white/80 hover:text-white"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleDeleteConfirm}
                disabled={deleteConfirmTitle !== project.title}
                className="rounded px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50 disabled:cursor-not-allowed"
                style={{ backgroundColor: deleteConfirmTitle === project.title ? '#EF4444' : '#6B7280' }}
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
