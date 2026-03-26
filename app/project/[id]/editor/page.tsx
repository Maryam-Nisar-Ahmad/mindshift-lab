'use client';

import { useParams, useRouter } from 'next/navigation';
import { useState, useEffect, useRef, useCallback } from 'react';
import { getProject, getSegments, getEditorState, saveEditorState, fileUrl, generateSubtitles, exportVideo, getJobStatus, stepComplete } from '@/lib/api';
import useToast from '@/hooks/useToast';
import Toast from '@/components/Toast';

type Segment = {
  segment_id: number;
  image: string;
  start: number;
  end: number;
  text?: string;
  matched: boolean;
  match_ratio: number;
};

type SubtitleLine = {
  start: number;
  end: number;
  text: string;
  words: { word: string; start: number; end: number }[];
};

type Transition = {
  type: 'none' | 'fade';
  duration: number;
};

type SubtitleSettings = {
  font_size: number;
  position: string;
  positionY: number;
  color: string;
  highlight_color: string;
  highlight_enabled: boolean;
  outline_width: number;
};

type Project = { id: string; title: string };

const DEFAULT_SUBTITLE_SETTINGS: SubtitleSettings = {
  font_size: 48,
  position: 'bottom',
  positionY: 88,
  color: '#FFFFFF',
  highlight_color: '#FFFF00',
  highlight_enabled: true,
  outline_width: 4,
};

export default function EditorPage() {
  const params = useParams();
  const router = useRouter();
  const id = typeof params.id === 'string' ? params.id : '';
  const { toasts, showToast, removeToast } = useToast();

  const [segments, setSegments] = useState<Segment[]>([]);
  const [subtitles, setSubtitles] = useState<SubtitleLine[]>([]);
  const [transitions, setTransitions] = useState<Record<string, Transition>>({});
  const [subtitleSettings, setSubtitleSettings] = useState<SubtitleSettings>(DEFAULT_SUBTITLE_SETTINGS);
  const [hasSubtitles, setHasSubtitles] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [totalDuration, setTotalDuration] = useState(0);
  const [project, setProject] = useState<Project | null>(null);
  const [imagesLoaded, setImagesLoaded] = useState(false);
  const [preloadedImages, setPreloadedImages] = useState<Record<string, HTMLImageElement>>({});
  const [timelineContainerWidth, setTimelineContainerWidth] = useState(800);
  const [showExportOverlay, setShowExportOverlay] = useState(false);
  const [exportJobId, setExportJobId] = useState<string | null>(null);
  const [exportStatus, setExportStatus] = useState<string>('');
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportDone, setExportDone] = useState(false);
  const [dataLoaded, setDataLoaded] = useState(false);
  const [subtitlesPanelOpen, setSubtitlesPanelOpen] = useState(true);
  const [transitionsPanelOpen, setTransitionsPanelOpen] = useState(true);
  const [globalFadeDuration, setGlobalFadeDuration] = useState(0.5);
  const [timelineZoom, setTimelineZoom] = useState(1);
  const [zoomStep, setZoomStep] = useState(0.5);
  const [playbackSpeed, setPlaybackSpeed] = useState(1);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number | null>(null);
  const lastTimeRef = useRef<number>(0);
  const timelineInnerRef = useRef<HTMLDivElement>(null);
  const timelineOuterRef = useRef<HTMLDivElement>(null);
  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const editorStateRef = useRef({ segments, subtitles, transitions, subtitleSettings });
  editorStateRef.current = { segments, subtitles, transitions, subtitleSettings };
  const dragRef = useRef<{ startX: number; startStart: number; startEnd: number; segmentId: number; side: 'left' | 'right' } | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const replaceInputRef = useRef<HTMLInputElement>(null);
  const addInputRef = useRef<HTMLInputElement>(null);
  const [replacingSegmentId, setReplacingSegmentId] = useState<number | null>(null);
  const [selectedSegmentId, setSelectedSegmentId] = useState<number | null>(null);
  const [movingSegmentId, setMovingSegmentId] = useState<number | null>(null);
  const [isSnapping, setIsSnapping] = useState(false);
  const [parkedSegments, setParkedSegments] = useState<Segment[]>([]);
  const [history, setHistory] = useState<any[]>([]);
  const [historyIndex, setHistoryIndex] = useState<number>(-1);

  useEffect(() => {
    if (!id) return;
    const load = async () => {
      try {
        const [p, segs, editorState] = await Promise.all([
          getProject(id).then((x: Project) => x ?? null),
          getSegments(id).then((s: Segment[]) => Array.isArray(s) ? s : []),
          getEditorState(id).then((e: Record<string, unknown>) => e ?? {}),
        ]);
        setProject(p);
        const stateSegs = (editorState.segments as Segment[] | undefined);
        const finalSegments = (stateSegs && stateSegs.length > 0 ? stateSegs : segs) as Segment[];
        setSegments(finalSegments);
        setParkedSegments((editorState.parkedSegments as Segment[] | undefined) || []);

        const stateSubtitles = editorState.subtitles as SubtitleLine[] | undefined;
        if (stateSubtitles && stateSubtitles.length > 0) {
          setSubtitles(stateSubtitles);
          setHasSubtitles(true);
        }
        const stateTransitions = editorState.transitions as Record<string, Transition> | undefined;
        if (stateTransitions && Object.keys(stateTransitions).length > 0) {
          setTransitions(stateTransitions);
        }
        const stateSettings = editorState.subtitleSettings as Partial<SubtitleSettings> | undefined;
        if (stateSettings && (stateSettings.font_size != null || stateSettings.position != null || stateSettings.color != null)) {
          setSubtitleSettings({ ...DEFAULT_SUBTITLE_SETTINGS, ...stateSettings });
        }

        const duration = finalSegments.length > 0
          ? Math.max(...finalSegments.map((seg) => seg.end)) + 1.5
          : 0;
        setTotalDuration(duration);
        setHistory([]);
        setHistoryIndex(-1);
        setTimeout(() => {
          saveToHistory();
        }, 0);
      } catch (err) {
        showToast(err instanceof Error ? err.message : 'Request failed', 'error');
      } finally {
        setDataLoaded(true);
      }
    };
    load();
  }, [id, showToast]);

  useEffect(() => {
    if (!id || segments.length === 0) {
      setImagesLoaded(true);
      return;
    }
    const images: Record<string, HTMLImageElement> = {};
    let loaded = 0;
    const total = segments.length;
    segments.forEach((seg) => {
      const img = new Image();
      const path = seg.image.startsWith('images/') ? seg.image : `images/${seg.image}`;
      img.onload = () => {
        images[seg.segment_id.toString()] = img;
        loaded += 1;
        if (loaded >= total) {
          setPreloadedImages((prev) => ({ ...prev, ...images }));
          setImagesLoaded(true);
        }
      };
      img.onerror = () => {
        loaded += 1;
        if (loaded >= total) setImagesLoaded(true);
      };
      img.src = fileUrl(id, path);
    });
  }, [id, segments]);

  const renderFrame = useCallback((time: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const width = 1280;
    const height = 720;

    const activeSeg = segments.find((s) => s.start <= time && time < s.end);
    if (activeSeg) {
      const img = preloadedImages[activeSeg.segment_id.toString()];
      if (img && img.complete && img.naturalWidth > 0) {
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        // Check if we are in a fade transition zone
        const segIndex = segments.findIndex(s => s.start <= time && time < s.end);
        if (segIndex >= 0 && segIndex < segments.length - 1) {
          const seg = segments[segIndex];
          const nextSeg = segments[segIndex + 1];
          const transKey = String(segIndex) + '-' + String(segIndex + 1);
          const trans = transitions[transKey];
          if (trans && trans.type === 'fade') {
            const fadeDur = trans.duration || 0.5;
            const fadeStart = seg.end - fadeDur;
            if (time >= fadeStart) {
              const alpha = (time - fadeStart) / fadeDur;
              const nextImg = preloadedImages[nextSeg.segment_id.toString()];
              if (nextImg) {
                ctx.globalAlpha = alpha;
                ctx.drawImage(nextImg, 0, 0, canvas.width, canvas.height);
                ctx.globalAlpha = 1.0;
              }
            }
          }
        }
      } else {
        ctx.fillStyle = '#000000';
        ctx.fillRect(0, 0, width, height);
      }
    } else {
      ctx.fillStyle = '#000000';
      ctx.fillRect(0, 0, width, height);
    }

    const activeLine = subtitles.find(l => l.start <= time && time < l.end);
    if (activeLine) {
      const scaledFontSize = Math.round((subtitleSettings.font_size || 48) * (canvas.width / 1920));
      ctx.font = `bold ${scaledFontSize}px Arial`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      const y = canvas.height * ((subtitleSettings.positionY ?? 88) / 100);
      const baseColor = subtitleSettings.color || '#FFFFFF';
      const highlightColor = subtitleSettings.highlight_color || '#FFFF00';
      const highlightEnabled = subtitleSettings.highlight_enabled !== false;
      const words = activeLine.words || [];
      if (!highlightEnabled || words.length === 0) {
        const outlineWidth = subtitleSettings.outline_width ?? 4;
        if (outlineWidth > 0) {
          ctx.strokeStyle = '#000000';
          ctx.lineWidth = outlineWidth * (canvas.width / 1920);
          ctx.strokeText(activeLine.text, canvas.width / 2, y);
        }
        ctx.fillStyle = baseColor;
        ctx.fillText(activeLine.text, canvas.width / 2, y);
      } else {
        const outlineWidth = subtitleSettings.outline_width ?? 4;
        const totalWidth = ctx.measureText(activeLine.text).width;
        let x = canvas.width / 2 - totalWidth / 2;
        ctx.textAlign = 'left';
        words.forEach((w, i) => {
          const wordWithSpace = i < words.length - 1 ? w.word + ' ' : w.word;
          const isActive = w.start <= time && time < w.end;
          if (outlineWidth > 0) {
            ctx.strokeStyle = '#000000';
            ctx.lineWidth = outlineWidth * (canvas.width / 1920);
            ctx.strokeText(wordWithSpace, x, y);
          }
          ctx.fillStyle = isActive ? highlightColor : baseColor;
          ctx.fillText(wordWithSpace, x, y);
          x += ctx.measureText(wordWithSpace).width;
        });
        ctx.textAlign = 'center';
      }
    }
  }, [segments, subtitles, subtitleSettings, preloadedImages, transitions]);

  useEffect(() => {
    renderFrame(currentTime);
  }, [currentTime, segments, subtitles, renderFrame]);

  useEffect(() => {
    if (!isPlaying) {
      if (rafRef.current != null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      if (audioRef.current) {
        audioRef.current.pause();
      }
      return;
    }
    if (audioRef.current) {
      audioRef.current.currentTime = currentTime;
      audioRef.current.playbackRate = playbackSpeed;
      audioRef.current.play();
    }
    lastTimeRef.current = performance.now();
    const loop = () => {
      const now = performance.now();
      const delta = ((now - lastTimeRef.current) / 1000) * playbackSpeed;
      lastTimeRef.current = now;
      setCurrentTime((prev) => {
        const newTime = prev + delta;
        if (newTime >= totalDuration) {
          setIsPlaying(false);
          return totalDuration;
        }
        return newTime;
      });
      rafRef.current = requestAnimationFrame(loop);
    };
    rafRef.current = requestAnimationFrame(loop);
    return () => {
      if (rafRef.current != null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      if (audioRef.current) {
        audioRef.current.pause();
      }
    };
  }, [isPlaying, totalDuration, playbackSpeed]);

  useEffect(() => {
    if (!isPlaying) return;
    const outer = timelineOuterRef.current;
    if (!outer) return;
    const innerWidth = timelineContainerWidth * timelineZoom;
    const playheadX = (currentTime / totalDuration) * innerWidth;
    const visibleWidth = outer.clientWidth;
    const scrollLeft = outer.scrollLeft;
    if (playheadX < scrollLeft + visibleWidth * 0.1 || playheadX > scrollLeft + visibleWidth * 0.9) {
      outer.scrollLeft = playheadX - visibleWidth * 0.5;
    }
  }, [currentTime, isPlaying, totalDuration, timelineZoom, timelineContainerWidth]);

  useEffect(() => {
    if (!imagesLoaded || !canvasRef.current) return;
    const canvas = canvasRef.current;
    const firstSeg = segments[0];
    if (firstSeg && preloadedImages[firstSeg.segment_id.toString()]) {
      const img = preloadedImages[firstSeg.segment_id.toString()];
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
    } else {
      canvas.width = 1920;
      canvas.height = 1080;
    }
  }, [imagesLoaded, segments, preloadedImages]);

  useEffect(() => {
    if (isPlaying) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const activeSeg = segments.find(s => s.start <= currentTime && currentTime < s.end)
      || (currentTime >= (segments[segments.length - 1]?.end ?? 0) ? segments[segments.length - 1] : segments[0]);
    if (!activeSeg) return;
    const img = preloadedImages[activeSeg.segment_id.toString()];
    if (img) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    }
  }, [currentTime, isPlaying, segments, preloadedImages]);

  useEffect(() => {
    const el = timelineOuterRef.current;
    if (!el) return;
    const update = () => setTimelineContainerWidth(el.clientWidth);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === ' ') {
        e.preventDefault();
        setIsPlaying(prev => !prev);
        return;
      }
      if (e.key === 'ArrowRight') {
        e.preventDefault();
        setCurrentTime(prev => Math.min(totalDuration, parseFloat((prev + 0.1).toFixed(2))));
      }
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        setCurrentTime(prev => Math.max(0, parseFloat((prev - 0.1).toFixed(2))));
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [totalDuration]);

  const formatTime = (t: number) => {
    const m = Math.floor(t / 60);
    const s = Math.floor(t % 60);
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  };

  const updateTotalDuration = useCallback(() => {
    const maxEnd = segments.length > 0
      ? Math.max(...segments.map((seg) => seg.end))
      : 0;
    setTotalDuration(maxEnd + 1.5);
  }, [segments]);

  // Undo/Redo helpers - Step 7 update
  const saveToHistory = useCallback(() => {
    const currentState = {
      segments: JSON.parse(JSON.stringify(segments)),
      parkedSegments: JSON.parse(JSON.stringify(parkedSegments)),
      transitions: JSON.parse(JSON.stringify(transitions)),
      // We will expand this later for transitions, subtitles, etc.
    };
    setHistory((prev) => {
      const newHistory = prev.slice(0, historyIndex + 1);
      newHistory.push(currentState);
      setHistoryIndex(newHistory.length - 1);
      return newHistory;
    });
  }, [segments, parkedSegments, transitions, historyIndex]);

  const performUndo = useCallback(() => {
    if (historyIndex <= 0) return;
    const previousState = history[historyIndex - 1];
    if (!previousState) return;

    setSegments(previousState.segments);
    setParkedSegments(previousState.parkedSegments || []);
    setTransitions(previousState.transitions || {});
    updateTotalDuration();

    setHistoryIndex(prev => prev - 1);
  }, [history, historyIndex, setSegments, setParkedSegments, setTransitions]);

  const performRedo = useCallback(() => {
    if (historyIndex >= history.length - 1) return;
    const nextState = history[historyIndex + 1];
    if (!nextState) return;

    setSegments(nextState.segments);
    setParkedSegments(nextState.parkedSegments || []);
    setTransitions(nextState.transitions || {});
    updateTotalDuration();

    setHistoryIndex(prev => prev + 1);
  }, [history, historyIndex, setSegments, setParkedSegments, setTransitions]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        if (e.shiftKey) {
          performRedo();
        } else {
          performUndo();
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [performUndo, performRedo]);

  const autoSave = useCallback(() => {
    if (!id) return;
    if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    autoSaveTimerRef.current = setTimeout(() => {
      autoSaveTimerRef.current = null;
      const editorState = {
        ...editorStateRef.current,
        // parkedSegments persisted for parking track feature
        parkedSegments,
      };
      saveEditorState(id, editorState);
    }, 1000);
  }, [id, parkedSegments]);

  const handleEdit = useCallback(
    (idx: number, newText: string) => {
      setSubtitles((prev) => {
        const next = [...prev];
        const line = next[idx];
        if (!line) return prev;
        const words = newText.trim().split(/\s+/).filter(Boolean);
        const start = line.start;
        const end = line.end;
        const span = end - start;
        const wordDuration = words.length > 0 ? span / words.length : 0;
        const newWords = words.map((word, i) => ({
          word,
          start: start + i * wordDuration,
          end: start + (i + 1) * wordDuration,
        }));
        next[idx] = { ...line, text: newText.trim(), words: newWords };
        return next;
      });
      autoSave();
    },
    [autoSave]
  );

  const handleBack = () => router.push(`/project/${id}`);

  const buildEditorState = () => ({
    segments: segments.map((seg, idx, arr) => {
      if (idx === arr.length - 1) {
        return { ...seg, end: seg.end + 1.5 };
      }
      return seg;
    }),
    parkedSegments,
    transitions,
    subtitles,
    has_subtitles: hasSubtitles,
    font_size: subtitleSettings.font_size,
    alignment: subtitleSettings.position,
    subtitleSettings,
    canvas_display_width: canvasRef.current ? canvasRef.current.offsetWidth : 1920,
    totalDuration: totalDuration,
    end_padding_seconds: 1.5,
  });

  const handleExportClick = async () => {
    if (!id) return;
    setShowExportOverlay(true);
    setExportError(null);
    setExportDone(false);
    setExportStatus('Starting…');
    try {
      const state = buildEditorState();
      const res = await exportVideo(id, state) as { job_id?: string };
      if (res?.job_id) {
        setExportJobId(res.job_id);
        setExportStatus('Exporting…');
      } else {
        setExportError('No job ID returned');
        setExportStatus('');
      }
    } catch (err) {
      setExportError(err instanceof Error ? err.message : 'Export failed');
      setExportStatus('');
    }
  };

  useEffect(() => {
    if (!exportJobId) return;
    const interval = setInterval(async () => {
      try {
        const s = await getJobStatus(exportJobId) as { status?: string; percent?: number; error?: string };
        if (s?.status === 'done') {
          setExportDone(true);
          setExportStatus('Done');
          setExportJobId(null);
        } else if (s?.status === 'error') {
          setExportError(s?.error ?? 'Export failed');
          setExportJobId(null);
        } else {
          setExportStatus(s?.status === 'pending' ? 'Exporting…' : (s?.status ?? 'Exporting…'));
        }
      } catch {
        setExportError('Failed to get status');
        setExportJobId(null);
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [exportJobId]);

  if (!project) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: '#0D1A12' }}>
        <p className="text-white/70">Loading…</p>
      </div>
    );
  }

  if (dataLoaded && segments.length === 0) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4" style={{ backgroundColor: '#0D1A12' }}>
        <p className="text-white/70">No segments — go back and run Assemble</p>
        <button
          type="button"
          onClick={handleBack}
          className="rounded px-4 py-2 text-sm font-medium text-white"
          style={{ backgroundColor: '#3DBA6F' }}
        >
          Back
        </button>
      </div>
    );
  }

  if (dataLoaded && totalDuration === 0 && segments.length > 0) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4" style={{ backgroundColor: '#0D1A12' }}>
        <p className="text-white/70">No audio — go back and process audio</p>
        <button
          type="button"
          onClick={handleBack}
          className="rounded px-4 py-2 text-sm font-medium text-white"
          style={{ backgroundColor: '#3DBA6F' }}
        >
          Back
        </button>
      </div>
    );
  }

  return (
    <>
      <audio ref={audioRef} src={fileUrl(id, 'audio.mp3')} preload="auto" className="hidden" />
      <header
        className="fixed top-0 left-0 right-0 z-10 flex h-14 items-center px-6"
        style={{ backgroundColor: '#132419' }}
      >
        <button
          type="button"
          onClick={handleBack}
          className="mr-3 text-white/80 hover:text-white"
          aria-label="Back"
        >
          ←
        </button>
        <span className="text-white flex-1">Editor — {project.title}</span>
        <button
          type="button"
          onClick={handleExportClick}
          className="text-white/80 hover:text-white text-sm"
        >
          Export
        </button>
      </header>

      <main className="pt-14" style={{ backgroundColor: '#0D1A12' }}>
        <div className="flex justify-center p-4">
          <input
            ref={replaceInputRef}
            type="file"
            accept="image/png,image/jpeg"
            style={{ display: 'none' }}
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file || replacingSegmentId === null) return;
              const seg =
                segments.find(s => s.segment_id === replacingSegmentId) ??
                parkedSegments.find(s => s.segment_id === replacingSegmentId);
              if (!seg) return;
              const imageName = seg.image;
              const formData = new FormData();
              formData.append('file', file);
              await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/projects/${params.id}/replace-image/${imageName}`, {
                method: 'POST',
                body: formData,
              });
              const newUrl = `${process.env.NEXT_PUBLIC_API_URL}/api/projects/${params.id}/files/images/${imageName}?t=${Date.now()}`;
              saveToHistory();
              setSegments(prev =>
                prev.map(s =>
                  s.segment_id === replacingSegmentId
                    ? { ...s, image: imageName }
                    : s
                )
              );
              updateTotalDuration();
              const img = new Image();
              img.src = newUrl;
              img.onload = () => {
                setPreloadedImages(prev => ({ ...prev, [replacingSegmentId.toString()]: img }));
              };
              setReplacingSegmentId(null);
              if (replaceInputRef.current) replaceInputRef.current.value = '';
            }}
          />
          <input
            ref={addInputRef}
            type="file"
            accept="image/png,image/jpeg"
            style={{ display: 'none' }}
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file || selectedSegmentId === null) return;
              const idx = segments.findIndex(s => s.segment_id === selectedSegmentId);
              if (idx === -1) return;
              const newId = Date.now();
              const newImageName = `${newId}.png`;
              const formData = new FormData();
              formData.append('file', file);
              await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/projects/${params.id}/replace-image/${newImageName}`, {
                method: 'POST',
                body: formData,
              });
              const insertAfterSeg = segments[idx];
              const newSegStart = insertAfterSeg.end;
              const newSegEnd = newSegStart + 4.0;
              const newSeg = {
                segment_id: newId,
                image: newImageName,
                start: newSegStart,
                end: newSegEnd,
                text: '',
                matched: true,
                match_ratio: 1,
              };
              saveToHistory();
              setSegments(prev => {
                const next = [...prev];
                next.splice(idx + 1, 0, newSeg);
                return next;
              });
              updateTotalDuration();
              const newUrl = `${process.env.NEXT_PUBLIC_API_URL}/api/projects/${params.id}/files/images/${newImageName}?t=${Date.now()}`;
              const img = new Image();
              img.src = newUrl;
              img.onload = () => {
                setPreloadedImages(prev => ({ ...prev, [newId.toString()]: img }));
              };
              if (addInputRef.current) addInputRef.current.value = '';
              autoSave();
            }}
          />
          <canvas
            ref={canvasRef}
            onClick={() => setIsPlaying(prev => !prev)}
            style={{ width: '100%', maxWidth: '860px', aspectRatio: '16/9', imageRendering: 'auto', display: 'block', margin: '0 auto', cursor: 'pointer' }}
            className="block bg-black"
          />
        </div>

        {/* Active scene info line */}
        {(() => {
          const activeSeg = segments.find(
            (s) => s.start <= currentTime && currentTime < s.end
          ) ?? (segments.length > 0 ? segments[0] : null);
          if (!activeSeg) return null;
          const sceneIdx = segments.findIndex(
            (s) => s.segment_id === activeSeg.segment_id
          );
          return (
            <div
              className="px-4 pb-1 py-2 flex items-center gap-2 flex-wrap bg-black/40 rounded-md"
              style={{ minHeight: '24px' }}
            >
              <span
                className="text-lg font-medium px-3 py-1 rounded-md"
                style={{ backgroundColor: '#132419', color: '#3DBA6F' }}
              >
                Scene {sceneIdx + 1}
              </span>
              {activeSeg.text ? (
                <span className="text-base" style={{ color: '#F0F4F0', opacity: 0.85 }}>
                  {activeSeg.text}
                </span>
              ) : null}
              {activeSeg.matched === false && (
                <span
                  className="text-xs px-1.5 py-0.5 rounded"
                  style={{ backgroundColor: '#6B5A1A', color: '#F0C040' }}
                >
                  ⚠ Unmatched — adjust position manually
                </span>
              )}
            </div>
          );
        })()}
        <div className="px-4 pb-4 flex items-center gap-4 flex-wrap">
          <button
            type="button"
            onClick={() => setIsPlaying((p) => !p)}
            className="rounded px-4 py-2 text-sm font-medium text-white"
            style={{ backgroundColor: '#3DBA6F' }}
          >
            {isPlaying ? 'Pause' : 'Play'}
          </button>
          <span className="text-white font-mono text-sm">
            {formatTime(currentTime)}
          </span>
          <select
            value={playbackSpeed}
            onChange={e => setPlaybackSpeed(Number(e.target.value))}
            className="rounded bg-[#0D1A12] border border-[#2A4A35] text-[#F0F4F0] text-xs px-2 py-1 ml-2"
          >
            <option value={0.25}>0.25x</option>
            <option value={0.5}>0.5x</option>
            <option value={0.75}>0.75x</option>
            <option value={1}>1x</option>
            <option value={1.25}>1.25x</option>
            <option value={1.5}>1.5x</option>
            <option value={2}>2x</option>
          </select>
          <input
            type="range"
            min={0}
            max={totalDuration || 1}
            step={0.1}
            value={currentTime}
            onChange={(e) => {
              const newTime = Number(e.target.value);
              setCurrentTime(newTime);
              if (audioRef.current) {
                audioRef.current.currentTime = newTime;
              }
            }}
            className="flex-1 min-w-[120px] accent-[#3DBA6F]"
          />
        </div>

        <div className="mx-4 mt-4 flex gap-4">
          <div className="flex-[0.6] flex flex-col">
            <div className="flex items-center justify-between mb-3">
              <span className="text-[#F0F4F0] font-semibold">Subtitles</span>
              <button
                onClick={() => setSubtitlesPanelOpen(o => !o)}
                className="text-[#8FAF96] hover:text-[#F0F4F0] text-xs px-2 py-1 rounded border border-[#2A4A35] hover:border-[#3DBA6F] transition-colors"
              >
                {subtitlesPanelOpen ? '▲ Hide' : '▼ Show'}
              </button>
            </div>
            {subtitlesPanelOpen && (
            <>
            {!hasSubtitles ? (
              <button
                type="button"
                onClick={async () => {
                  try {
                    const result = (await generateSubtitles(id)) as SubtitleLine[];
                    if (Array.isArray(result)) {
                      setSubtitles(result);
                      setHasSubtitles(true);
                      autoSave();
                    }
                  } catch (err) {
                    showToast(err instanceof Error ? err.message : 'Request failed', 'error');
                  }
                }}
                className="rounded px-4 py-2 text-sm font-medium text-white w-fit"
                style={{ backgroundColor: '#3DBA6F' }}
              >
                Generate Subtitles
              </button>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-3 mb-2">
                  <label className="flex items-center gap-2">
                    <span className="text-white/80 text-sm">Font Size</span>
                    <input
                      type="range"
                      min={24}
                      max={72}
                      value={subtitleSettings.font_size}
                      onChange={(e) => {
                        setSubtitleSettings((s) => ({ ...s, font_size: Number(e.target.value) }));
                        autoSave();
                      }}
                      className="accent-[#3DBA6F]"
                    />
                    <span className="text-[#8FAF96] text-xs font-mono w-6">{subtitleSettings.font_size}</span>
                  </label>
                  <label className="flex items-center gap-2">
                    <span className="text-[#8FAF96] text-xs">Y Position %</span>
                    <input
                      type="number"
                      min={0}
                      max={100}
                      value={subtitleSettings.positionY ?? 88}
                      onChange={(e) => {
                        setSubtitleSettings((prev) => ({ ...prev, positionY: Number(e.target.value) }));
                        autoSave();
                      }}
                      className="w-16 bg-[#0D1A12] border border-[#2A4A35] rounded text-[#F0F4F0] text-xs px-2 py-1"
                    />
                  </label>
                  <label className="flex items-center gap-2">
                    <span className="text-white/80 text-sm">Color</span>
                    <input
                      type="color"
                      value={subtitleSettings.color}
                      onChange={(e) => {
                        setSubtitleSettings((s) => ({ ...s, color: e.target.value }));
                        autoSave();
                      }}
                      className="w-8 h-8 rounded border border-[#2A4A35] cursor-pointer"
                    />
                  </label>
                  <label className="flex items-center gap-2">
                    <span className="text-white/80 text-sm">Highlight</span>
                    <input
                      type="checkbox"
                      checked={subtitleSettings.highlight_enabled}
                      onChange={e => setSubtitleSettings(s => ({...s, highlight_enabled: e.target.checked}))}
                    />
                    <input
                      type="color"
                      value={subtitleSettings.highlight_color}
                      onChange={e => setSubtitleSettings(s => ({...s, highlight_color: e.target.value}))}
                      style={{ width: '32px', height: '24px', padding: 0, border: 'none', background: 'none', cursor: 'pointer' }}
                    />
                  </label>
                  <label className="flex items-center gap-2">
                    <span className="text-white/80 text-sm">Outline</span>
                    <input
                      type="range"
                      min={0}
                      max={12}
                      step={1}
                      value={subtitleSettings.outline_width ?? 4}
                      onChange={e => setSubtitleSettings(s => ({...s, outline_width: Number(e.target.value)}))}
                      className="w-24 accent-[#3DBA6F]"
                    />
                    <span className="text-white/60 text-xs">{subtitleSettings.outline_width ?? 4}px</span>
                  </label>
                </div>
                <div className="max-h-64 overflow-y-auto space-y-1">
                  {subtitles.map((line, idx) => (
                    <div
                      key={idx}
                      className="rounded p-2 flex gap-3 cursor-pointer hover:bg-[#0D1A12]"
                      onClick={() => setCurrentTime(line.start)}
                    >
                      <span className="text-[#8FAF96] text-xs font-mono flex-shrink-0">
                        {formatTime(line.start)}
                      </span>
                      <span
                        contentEditable
                        suppressContentEditableWarning
                        className="text-[#F0F4F0] flex-1 outline-none"
                        onBlur={(e) => handleEdit(idx, e.currentTarget.textContent ?? '')}
                      >
                        {line.text}
                      </span>
                    </div>
                  ))}
                </div>
              </>
            )}
            </>
            )}
          </div>
          <div className="flex-[0.4] flex flex-col">
            <div className="flex items-center justify-between mb-3">
              <span className="text-[#F0F4F0] font-semibold">Transitions</span>
              <button
                onClick={() => setTransitionsPanelOpen(o => !o)}
                className="text-[#8FAF96] hover:text-[#F0F4F0] text-xs px-2 py-1 rounded border border-[#2A4A35] hover:border-[#3DBA6F] transition-colors"
              >
                {transitionsPanelOpen ? '▲ Hide' : '▼ Show'}
              </button>
            </div>
            {transitionsPanelOpen && (
            <>
            <div className="flex items-center gap-2 mb-2 flex-wrap">
              <select
                value={globalFadeDuration}
                onChange={e => setGlobalFadeDuration(Number(e.target.value))}
                className="rounded bg-[#0D1A12] border border-[#2A4A35] text-[#F0F4F0] text-xs px-2 py-1"
              >
                <option value={0.1}>0.1s</option>
                <option value={0.2}>0.2s</option>
                <option value={0.3}>0.3s</option>
                <option value={0.5}>0.5s</option>
                <option value={0.8}>0.8s</option>
                <option value={1.0}>1.0s</option>
                <option value={1.5}>1.5s</option>
              </select>
              <button
                type="button"
                onClick={() => {
                  const next: Record<string, Transition> = {};
                  segments.forEach((seg, i) => {
                    if (i < segments.length - 1) {
                      const key = String(i) + '-' + String(i + 1);
                      next[key] = { type: 'fade', duration: globalFadeDuration };
                    }
                  });
                  saveToHistory();
                  setTransitions(next);
                  autoSave();
                }}
                className="rounded border border-[#3DBA6F] text-[#3DBA6F] text-xs px-2 py-1 hover:bg-[#3DBA6F]/10"
              >
                Apply Fade to All
              </button>
              <button
                type="button"
                onClick={() => {
                  const next: Record<string, Transition> = {};
                  segments.forEach((seg, i) => {
                    if (i < segments.length - 1) {
                      const key = String(i) + '-' + String(i + 1);
                      next[key] = { type: 'none', duration: 0.5 };
                    }
                  });
                  saveToHistory();
                  setTransitions(next);
                  autoSave();
                }}
                className="rounded border border-[#EF4444] text-[#EF4444] text-xs px-2 py-1 hover:bg-[#EF4444]/10"
              >
                Unapply Fade to All
              </button>
            </div>
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {segments.length > 1 &&
                Array.from({ length: segments.length - 1 }, (_, i) => {
                  const key = `${i}-${i + 1}`;
                  const t = transitions[key] ?? { type: 'none' as const, duration: 0.5 };
                  return (
                    <div key={key} className="flex flex-col gap-1">
                      <span className="text-[#8FAF96] text-xs">
                        Scene {i + 1} -&gt; Scene {i + 2}
                      </span>
                      <select
                        value={t.type}
                        onChange={(e) => {
                          const type = e.target.value as 'none' | 'fade';
                          saveToHistory();
                          setTransitions((prev) => ({
                            ...prev,
                            [key]: { type, duration: type === 'fade' ? (t.duration ?? 0.5) : 0.5 },
                          }));
                          autoSave();
                        }}
                        className="rounded bg-[#0D1A12] border border-[#2A4A35] text-[#F0F4F0] text-sm px-2 py-1"
                      >
                        <option value="none">None</option>
                        <option value="fade">Fade</option>
                      </select>
                      {t.type === 'fade' && (
                        <input
                          type="range"
                          min={0.3}
                          max={1.5}
                          step={0.1}
                          value={t.duration ?? 0.5}
                          onChange={(e) => {
                            saveToHistory();
                            setTransitions((prev) => ({
                              ...prev,
                              [key]: { type: 'fade', duration: Number(e.target.value) },
                            }));
                            autoSave();
                          }}
                          className="accent-[#3DBA6F]"
                        />
                      )}
                    </div>
                  );
                })}
            </div>
            </>
            )}
          </div>
        </div>
        <div className="mt-4 mx-4 rounded-xl" style={{ backgroundColor: '#0A1510' }}>
          <div className="flex items-center gap-2 px-2 py-1">
            <span className="text-[#8FAF96] text-xs">Zoom</span>
            <button
              onClick={() => setTimelineZoom(z => Math.max(1, parseFloat((z - zoomStep).toFixed(1))))}
              className="text-[#8FAF96] hover:text-[#F0F4F0] text-xs px-2 py-0.5 rounded border border-[#2A4A35] hover:border-[#3DBA6F]"
            >−</button>
            <span className="text-[#8FAF96] text-xs w-8 text-center">{timelineZoom}x</span>
            <button
              onClick={() => setTimelineZoom(z => Math.min(10, parseFloat((z + zoomStep).toFixed(1))))}
              className="text-[#8FAF96] hover:text-[#F0F4F0] text-xs px-2 py-0.5 rounded border border-[#2A4A35] hover:border-[#3DBA6F]"
            >+</button>
            <button
              onClick={() => setTimelineZoom(1)}
              className="text-[#8FAF96] hover:text-[#F0F4F0] text-xs px-2 py-0.5 rounded border border-[#2A4A35] hover:border-[#3DBA6F]"
            >Reset</button>
            <input
              type="range"
              min={1}
              max={10}
              step={0.1}
              value={timelineZoom}
              onChange={e => setTimelineZoom(Number(e.target.value))}
              className="w-32 accent-[#3DBA6F]"
            />
            <select
              value={zoomStep}
              onChange={e => setZoomStep(Number(e.target.value))}
              className="rounded bg-[#0D1A12] border border-[#2A4A35] text-[#F0F4F0] text-xs px-1 py-0.5"
            >
              <option value={0.5}>0.5x step</option>
              <option value={1}>1x step</option>
              <option value={2}>2x step</option>
              <option value={3}>3x step</option>
            </select>
          </div>
          {selectedSegmentId !== null && (
            <div className="flex items-center gap-2 px-2 py-1 bg-[#132419] border-b border-[#2A4A35]">
              <span className="text-[#8FAF96] text-xs">Scene {(segments.findIndex(s => s.segment_id === selectedSegmentId) + 1)} selected</span>
              <button
                onClick={() => {
                  setReplacingSegmentId(selectedSegmentId);
                  replaceInputRef.current?.click();
                }}
                className="text-xs px-2 py-0.5 rounded border border-[#3DBA6F] text-[#3DBA6F] hover:bg-[#3DBA6F]/10"
              >⟳ Replace Image</button>
              <button
                onClick={() => {
                  saveToHistory();
                  setSegments(prev => prev.filter(s => s.segment_id !== selectedSegmentId));
                  updateTotalDuration();
                  setSelectedSegmentId(null);
                  autoSave();
                }}
                className="text-xs px-2 py-0.5 rounded border border-[#EF4444] text-[#EF4444] hover:bg-[#EF4444]/10"
              >✕ Delete Scene</button>
              <button
                onClick={() => {
                  addInputRef.current?.click();
                }}
                className="text-xs px-2 py-0.5 rounded border border-[#F59E0B] text-[#F59E0B] hover:bg-[#F59E0B]/10"
              >+ Add Scene After</button>
              <button
                onClick={() => setSelectedSegmentId(null)}
                className="text-xs px-2 py-0.5 rounded border border-[#2A4A35] text-[#8FAF96] hover:border-[#3DBA6F]"
              >Deselect</button>
            </div>
          )}
          <div
            ref={timelineOuterRef}
            className="overflow-x-auto rounded-xl relative"
            style={{ height: '4.8rem', backgroundColor: '#0A1510', overflowY: 'visible' }}
            onClick={(e) => {
              setMovingSegmentId(null);
              const rect = e.currentTarget.getBoundingClientRect();
              const scrollLeft = e.currentTarget.scrollLeft;
              const x = e.clientX - rect.left + scrollLeft;
              const innerWidth = timelineContainerWidth * timelineZoom;
              const newTime = (x / innerWidth) * totalDuration;
              const seekTime = Math.max(0, Math.min(totalDuration, newTime));
              setCurrentTime(seekTime);
              if (audioRef.current) {
                audioRef.current.currentTime = seekTime;
              }
            }}
          >
            <div
              ref={timelineInnerRef}
              className="relative h-full"
              style={{
                width: timelineContainerWidth * timelineZoom,
                minWidth: '100%',
                minHeight: '80px',
              }}
            >
              {totalDuration > 0 &&
                segments.map((seg, idx) => {
                const innerWidth = timelineContainerWidth * timelineZoom;
                const left = (seg.start / totalDuration) * innerWidth;
                const duration = seg.end - seg.start;
                const width = Math.max(40, (duration / totalDuration) * innerWidth);
                const onLeftMouseDown = (e: React.MouseEvent) => {
                  e.stopPropagation();
                  dragRef.current = { startX: e.clientX, startStart: seg.start, startEnd: seg.end, segmentId: seg.segment_id, side: 'left' };
                  const onMove = (e: MouseEvent) => {
                    if (!dragRef.current) return;
                    const innerEl = timelineInnerRef.current;
                    const w = innerEl ? innerEl.offsetWidth : innerWidth;
                    const delta = ((e.clientX - dragRef.current.startX) / w) * totalDuration;
                    let newStart = Math.max(0, Math.min(seg.end - 0.5, dragRef.current.startStart + delta));
                    const snapThresholdL = totalDuration * 0.01;
                    if (Math.abs(newStart - currentTime) < snapThresholdL) {
                      newStart = currentTime;
                    }
                    saveToHistory();
                    setSegments((prev) =>
                      prev.map((s) => (s.segment_id === dragRef.current!.segmentId ? { ...s, start: newStart } : s))
                    );
                    updateTotalDuration();
                  };
                  const onUp = () => {
                    window.removeEventListener('mousemove', onMove);
                    window.removeEventListener('mouseup', onUp);
                    dragRef.current = null;
                    autoSave();
                  };
                  window.addEventListener('mousemove', onMove);
                  window.addEventListener('mouseup', onUp);
                };
                const onRightMouseDown = (e: React.MouseEvent) => {
                  e.stopPropagation();
                  dragRef.current = { startX: e.clientX, startStart: seg.start, startEnd: seg.end, segmentId: seg.segment_id, side: 'right' };
                  const onMove = (e: MouseEvent) => {
                    if (!dragRef.current) return;
                    const innerEl = timelineInnerRef.current;
                    const w = innerEl ? innerEl.offsetWidth : innerWidth;
                    const delta = ((e.clientX - dragRef.current.startX) / w) * totalDuration;
                    let newEnd = Math.max(seg.start + 0.5, Math.min(totalDuration, dragRef.current.startEnd + delta));
                    const snapThresholdR = totalDuration * 0.01;
                    if (Math.abs(newEnd - currentTime) < snapThresholdR) {
                      newEnd = currentTime;
                    }
                    saveToHistory();
                    setSegments((prev) =>
                      prev.map((s) => (s.segment_id === dragRef.current!.segmentId ? { ...s, end: newEnd } : s))
                    );
                    updateTotalDuration();
                  };
                  const onUp = () => {
                    window.removeEventListener('mousemove', onMove);
                    window.removeEventListener('mouseup', onUp);
                    dragRef.current = null;
                    autoSave();
                  };
                  window.addEventListener('mousemove', onMove);
                  window.addEventListener('mouseup', onUp);
                };
                const imgPath = seg.image.startsWith('images/') ? seg.image : `images/${seg.image}`;
                const isUnmatched = seg.matched === false;
                const segBarStyle = {
                  left,
                  width,
                  height: '60%',
                  top: '20%',
                  backgroundColor: isUnmatched ? '#1A1508' : '#132419',
                  borderColor:
                    (movingSegmentId === seg.segment_id && isSnapping)
                      ? '#22C55E'
                      : isUnmatched
                        ? (selectedSegmentId === seg.segment_id ? '#3DBA6F' : '#6B5A1A')
                        : (selectedSegmentId === seg.segment_id ? '#3DBA6F' : '#2A4A35'),
                  borderStyle: isUnmatched ? 'dashed' : 'solid',
                  boxShadow: movingSegmentId === seg.segment_id
                    ? (isSnapping ? '0 0 0 3px #F0C040' : '0 0 0 2px #F0C040')
                    : selectedSegmentId === seg.segment_id ? '0 0 0 2px #3DBA6F' : 'none',
                  opacity: isUnmatched ? 0.75 : 1,
                  zIndex: (movingSegmentId === seg.segment_id ||
                           selectedSegmentId === seg.segment_id) ? 10 : 1,
                  cursor: movingSegmentId === seg.segment_id ? 'move' : undefined,
                };
                return (
                  <div
                    key={seg.segment_id}
                    className="absolute rounded border flex items-center gap-2 overflow-hidden"
                    onMouseDown={(e) => {
                      e.stopPropagation();

                      const originalClickX = e.clientX;
                      const startY = e.clientY;
                      const isFromMainTrack = true; // we'll use this

                      const segmentEl = e.currentTarget as HTMLElement;
                      const startX = e.clientX;
                      const startStart = seg.start;
                      const startEnd = seg.end;
                      const duration = startEnd - startStart;
                      const segmentId = seg.segment_id;

                      // Drag-vs-click discrimination
                      let didDrag = false;
                      let didPark = false;
                      let currentSeg: Segment | null = null;

                      const onMove = (e: MouseEvent) => {
                        const deltaX = e.clientX - startX;

                        // Drag-vs-click: start moving only after the pointer moves >5px.
                        if (!didDrag && Math.abs(deltaX) > 5) {
                          didDrag = true;
                          currentSeg =
                            segments.find((s) => s.segment_id === segmentId) ??
                            parkedSegments.find((p) => p.segment_id === segmentId) ??
                            null;
                          setMovingSegmentId(segmentId);
                        }
                        if (!didDrag) return;

                        const liveSnapSegments = segments;
                        const deltaY = e.clientY - startY;

                        // Parking logic - drag down > 50px (slightly higher threshold for intentional action)
                        if (deltaY > 50 && isFromMainTrack && !didPark) {
                          if (!currentSeg) return;
                          const segToPark = currentSeg;
                          didPark = true;
                          saveToHistory();
                          setSegments(prev => prev.filter(s => s.segment_id !== segmentId));
                          setParkedSegments(prev => [...prev, segToPark]); // preserve original start/end times
                          updateTotalDuration();
                          setMovingSegmentId(null);
                          setIsSnapping(false);
                          autoSave();
                          showToast('Parked!', 'success');
                          return; // stop further movement
                        }

                        const innerEl = timelineInnerRef.current;
                        const w = innerEl ? innerEl.offsetWidth : (timelineContainerWidth * timelineZoom);
                        const delta = ((e.clientX - startX) / w) * totalDuration;
                        const snapPoints = [
                          0,
                          totalDuration,
                          ...liveSnapSegments
                            .filter((s) => s.segment_id !== segmentId)
                            .flatMap((s) => [s.start, s.end]),
                        ];

                        let newStart = Math.max(0, Math.min(totalDuration - duration, startStart + delta));
                        let newEnd = newStart + duration;

                        /*
                         * Snap to neighboring clip edges (1% threshold)
                         */
                        const snapThreshold = totalDuration * 0.01;
                        let snapped = false;

                        let snappedLeft: number | null = null;
                        let snappedLeftDist = Infinity;
                        for (const p of snapPoints) {
                          const dist = Math.abs(newStart - p);
                          if (dist <= snapThreshold && dist < snappedLeftDist) {
                            snappedLeftDist = dist;
                            snappedLeft = p;
                          }
                        }
                        if (snappedLeft !== null) {
                          newStart = snappedLeft;
                          newEnd = newStart + duration;
                          snapped = true;
                        }

                        let snappedRight: number | null = null;
                        let snappedRightDist = Infinity;
                        for (const p of snapPoints) {
                          const dist = Math.abs(newEnd - p);
                          if (dist <= snapThreshold && dist < snappedRightDist) {
                            snappedRightDist = dist;
                            snappedRight = p;
                          }
                        }
                        if (snappedRight !== null) {
                          newEnd = snappedRight;
                          newStart = newEnd - duration; // preserve duration when snapping right edge
                          snapped = true;
                        }

                        if (snapped) {
                          setIsSnapping(true);
                        }
                        setSegments((prev) =>
                          prev.map((s) => s.segment_id === segmentId ? { ...s, start: newStart, end: newEnd } : s)
                        );
                      };

                      const onUp = (e: MouseEvent) => {
                        window.removeEventListener('mousemove', onMove);
                        window.removeEventListener('mouseup', onUp);

                        setMovingSegmentId(null);

                        if (!didDrag) {
                          e.stopPropagation();
                          setSelectedSegmentId(prev => prev === segmentId ? null : segmentId);
                          const rect = (segmentEl as HTMLElement).closest('[data-timeline-outer]')?.getBoundingClientRect();
                          const outerEl = timelineOuterRef.current;
                          if (outerEl) {
                            const outerRect = outerEl.getBoundingClientRect();
                            const scrollLeft = outerEl.scrollLeft;
                            const x = originalClickX - outerRect.left + scrollLeft;
                            const innerWidth = timelineContainerWidth * timelineZoom;
                            const newTime = (x / innerWidth) * totalDuration;
                            const seekTime = Math.max(0, Math.min(totalDuration, newTime));
                            setCurrentTime(seekTime);
                            if (audioRef.current) {
                              audioRef.current.currentTime = seekTime;
                            }
                          }
                        } else {
                          saveToHistory();
                          updateTotalDuration();
                          autoSave();
                        }

                        setIsSnapping(false);
                      };

                      window.addEventListener('mousemove', onMove);
                      window.addEventListener('mouseup', onUp);
                    }}
                    style={segBarStyle}
                  >
                    <div
                      className="absolute left-0 top-0 h-full w-2 cursor-ew-resize hover:opacity-100"
                      style={{ backgroundColor: '#3DBA6F', opacity: 0.4 }}
                      onMouseDown={onLeftMouseDown}
                    />
                    <div className="flex-1 flex items-center gap-2 pl-3 pr-3 min-w-0">
                      <img
                        src={fileUrl(id, imgPath)}
                        alt=""
                        className="object-cover flex-shrink-0"
                        width={40}
                        height={25}
                      />
                      <span className="text-xs text-white/80">Scene {idx + 1}</span>
                      <span className="text-xs text-white/60">{formatTime(duration)}</span>
                      {seg.matched === false && (
                        <span className="text-xs px-1.5 py-0.5 rounded bg-amber-500/80 text-black">Unmatched</span>
                      )}
                      {seg.matched === true && seg.match_ratio > 0 && seg.match_ratio < 75 && (
                        <span
                          className="text-xs px-1.5 py-0.5 rounded"
                          style={{ backgroundColor: '#3D2A00', color: '#F0C040' }}
                        >
                          ~{Math.round(seg.match_ratio)}%
                        </span>
                      )}
                    </div>
                    <div
                      className="absolute right-0 top-0 h-full w-2 cursor-ew-resize hover:opacity-100"
                      style={{ backgroundColor: '#3DBA6F', opacity: 0.4 }}
                      onMouseDown={onRightMouseDown}
                    />
                  </div>
                  );
                })}
              {totalDuration > 0 && (
                <div
                  className="absolute top-0 h-full pointer-events-none"
                  style={{
                    left: (currentTime / totalDuration) * timelineContainerWidth * timelineZoom,
                    width: 2,
                    backgroundColor: '#EF4444',
                  }}
                />
              )}
            </div>
            <div
              className="relative h-[60px] mt-4 border-t border-white/20 pointer-events-auto"
              style={{ display: parkedSegments.length > 0 ? 'block' : 'none' }}
              data-parking-track
            >
              <div className="absolute inset-0 bg-black/30 rounded pointer-events-none" />
              <div className="relative h-full" style={{ width: `${timelineContainerWidth * timelineZoom}px` }}>
                {parkedSegments.map((seg, idx) => {
                  const left = totalDuration > 0 ? `${(seg.start / totalDuration) * 100}%` : '0%';
                  const width = totalDuration > 0 ? `${((seg.end - seg.start) / totalDuration) * 100}%` : '0%';
                  const isUnmatched = seg.matched === false;
                  const segBarStyle = {
                    left,
                    width,
                    height: '50%',
                    top: '25%',
                    backgroundColor: isUnmatched ? '#1A1508' : '#132419',
                    borderColor:
                      (movingSegmentId === seg.segment_id && isSnapping)
                        ? '#22C55E'
                        : isUnmatched
                          ? (selectedSegmentId === seg.segment_id ? '#3DBA6F' : '#6B5A1A')
                          : (selectedSegmentId === seg.segment_id ? '#3DBA6F' : '#2A4A35'),
                    borderStyle: isUnmatched ? 'dashed' : 'solid',
                    boxShadow:
                      movingSegmentId === seg.segment_id
                        ? (isSnapping ? '0 0 0 3px #F0C040' : '0 0 0 2px #F0C040')
                        : selectedSegmentId === seg.segment_id ? '0 0 0 2px #3DBA6F' : 'none',
                    opacity: isUnmatched ? 0.75 : 1,
                    zIndex: (movingSegmentId === seg.segment_id || selectedSegmentId === seg.segment_id) ? 10 : 1,
                    cursor: movingSegmentId === seg.segment_id ? 'move' : undefined,
                  };
                  return (
                    <div
                      key={seg.segment_id}
                      className="absolute rounded border flex items-center gap-2 overflow-hidden"
                      onMouseDown={(e) => {
                        e.stopPropagation();

                        const originalClickX = e.clientX;
                        const startY = e.clientY;
                        const segmentId = seg.segment_id; // from map
                        const currentSeg = parkedSegments.find((p) => p.segment_id === segmentId);
                        if (!currentSeg) return;
                        setMovingSegmentId(segmentId);
                        setIsSnapping(false);
                        let didRestore = false;

                        const onMove = (moveE: MouseEvent) => {
                          const deltaY = moveE.clientY - startY;

                          if (!didRestore && deltaY < -50) {
                            didRestore = true;
                            saveToHistory();
                            setParkedSegments(prev => prev.filter(p => p.segment_id !== segmentId));
                            setSegments((prev) => {
                              const mainSegs = [...prev].sort((a, b) => a.start - b.start);
                              const duration = Math.max(0, currentSeg.end - currentSeg.start);
                              const desiredStart = currentSeg.start;
                              const desiredEnd = desiredStart + duration;

                              const desiredFitsWithinBounds = desiredStart >= 0 && desiredEnd <= totalDuration;
                              const desiredOverlaps = mainSegs.some(
                                (s) => !(desiredEnd <= s.start || desiredStart >= s.end)
                              );

                              let newStart: number;
                              if (duration <= 0) {
                                newStart = desiredStart;
                              } else if (desiredFitsWithinBounds && !desiredOverlaps) {
                                // Keep original start/end if possible (no overlap)
                                newStart = desiredStart;
                              } else {
                                // Find earliest gap that can fit the clip
                                let cursor = 0;
                                let foundStart: number | null = null;

                                for (const s of mainSegs) {
                                  const gapStart = cursor;
                                  const gapEnd = s.start;
                                  if (gapEnd - gapStart >= duration) {
                                    foundStart = gapStart;
                                    break;
                                  }
                                  cursor = Math.max(cursor, s.end);
                                }

                                newStart =
                                  foundStart != null ? foundStart : Math.max(0, totalDuration - duration);
                              }

                              const newEnd = newStart + duration;
                              const restoredSeg: Segment = { ...currentSeg, start: newStart, end: newEnd };
                              return [...prev, restoredSeg].sort((a, b) => a.start - b.start);
                            });
                            updateTotalDuration();
                            setMovingSegmentId(null);
                            setIsSnapping(false);
                            autoSave();
                            return;
                          }
                        };

                        const onUp = () => {
                          window.removeEventListener('mousemove', onMove);
                          window.removeEventListener('mouseup', onUp);

                          setMovingSegmentId(null);

                          if (!didRestore) {
                            setSelectedSegmentId(prev => prev === segmentId ? null : segmentId);

                            const outerEl = timelineOuterRef.current;
                            if (outerEl) {
                              const outerRect = outerEl.getBoundingClientRect();
                              const scrollLeft = outerEl.scrollLeft;
                              const x = originalClickX - outerRect.left + scrollLeft;
                              const innerWidth = timelineContainerWidth * timelineZoom;
                              const newTime = (x / innerWidth) * totalDuration;
                              const seekTime = Math.max(0, Math.min(totalDuration, newTime));
                              setCurrentTime(seekTime);
                              if (audioRef.current) {
                                audioRef.current.currentTime = seekTime;
                              }
                            }
                          }
                        };

                        window.addEventListener('mousemove', onMove);
                        window.addEventListener('mouseup', onUp);
                      }}
                      style={segBarStyle}
                    >
                      <div className="flex-1 flex items-center gap-2 pl-3 pr-3 min-w-0">
                        <img
                          src={fileUrl(id, `images/${seg.image}`)}
                          alt=""
                          className="object-cover flex-shrink-0"
                          width={32}
                          height={20}
                        />
                        <span className="text-xs text-white/70">PARKED Scene {idx + 1}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="absolute -top-3 left-1/2 -translate-x-1/2 text-[10px] text-white/50 tracking-widest">
                PARKING TRACK (drag clips here to park • drag up to restore)
              </div>
            </div>
          </div>
        </div>
      </main>

      {showExportOverlay && (
        <div className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center">
          <div className="rounded-2xl p-8 w-96" style={{ backgroundColor: '#132419' }}>
            <p className="text-white mb-2">{exportStatus || 'Preparing…'}</p>
            {!exportError && !exportDone && (
              <div className="h-2 w-full rounded-full bg-white/20 overflow-hidden mb-4">
                <div className="h-full bg-[#3DBA6F] animate-pulse" style={{ width: '60%' }} />
              </div>
            )}
            {exportError && <p className="text-red-500 text-sm mb-4">{exportError}</p>}
            {exportDone && (
              <a
                href={fileUrl(id, 'output.mp4')}
                download
                target="_blank"
                rel="noreferrer"
                className="block text-[#3DBA6F] font-medium mb-4 hover:underline"
              >
                Download output.mp4
              </a>
            )}
            <div className="flex gap-3 justify-center">
              <button
                type="button"
                onClick={() => {
                  setShowExportOverlay(false);
                  setExportJobId(null);
                  setExportStatus('');
                  setExportError(null);
                  setExportDone(false);
                }}
                disabled={!exportDone && !exportError}
                className="rounded px-4 py-2 text-sm font-medium text-white disabled:opacity-50 disabled:cursor-not-allowed"
                style={{ backgroundColor: '#3DBA6F' }}
              >
                Close
              </button>
              {exportDone && (
                <button
                  type="button"
                  onClick={async () => { try { await stepComplete(id, 6); } catch(_) {} router.push(`/project/${id}#step-7`); }}
                  className="rounded px-4 py-2 text-sm font-medium text-white"
                  style={{ backgroundColor: '#2A4A35' }}
                >
                  Next Step →
                </button>
              )}
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
