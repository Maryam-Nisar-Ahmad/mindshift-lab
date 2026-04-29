'use client';

import { useParams, useRouter } from 'next/navigation';
import { useState, useEffect, useLayoutEffect, useRef, useCallback } from 'react';
import { getProject, getSegments, getEditorState, saveEditorState, fileUrl, generateSubtitles, exportVideo, getJobStatus, stepComplete } from '@/lib/api';
import { projectIdFromParams, encodeProjectIdForRoute, encodeProjectIdForPath } from '@/lib/projectId';
import { snapSubtitleLines } from '@/lib/subtitleTiming';
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
  /** Whole-track A/V sync fix (ms); subtracted from playhead when matching subtitle cues (export uses top-level key). */
  subtitle_offset_ms: number;
};

type Project = { id: string; title: string };
type HistorySnapshot = {
  segments: Segment[];
  parkedSegments: Segment[];
  transitions: Record<string, Transition>;
};

/** Snap `value` to the nearest time in `points` if within `threshold` (seconds). */
function snapTimeToNearest(value: number, points: number[], threshold: number): number {
  let best = value;
  let bestDist = Infinity;
  for (const p of points) {
    const d = Math.abs(value - p);
    if (d <= threshold && d < bestDist) {
      bestDist = d;
      best = p;
    }
  }
  return best;
}

/**
 * How close (seconds) a trim/drag must be to a snap point before it sticks.
 * Tighter than a flat % of the whole timeline (old 1% felt huge on long videos).
 */
function timelineSnapThresholdSec(totalDuration: number): number {
  if (!Number.isFinite(totalDuration) || totalDuration <= 0) return 0.04;
  const fromFraction = totalDuration * 0.0018;
  return Math.min(Math.max(fromFraction, 0.022), 0.065);
}

/** Matches ffmpeg export: 1920×1080, letterbox (scale with decrease + pad). */
const EXPORT_W = 1920;
const EXPORT_H = 1080;

/** `segments.json` may store `1.png` or `images/1.png`; API expects basename only. */
function imageFileBasename(image: string): string {
  const s = image.trim();
  const noPrefix = s.startsWith('images/') || s.startsWith('images\\') ? s.replace(/^images[/\\]/i, '') : s;
  return noPrefix.split(/[/\\]/).filter(Boolean).pop() || noPrefix;
}

function imagePathForFileApi(image: string): string {
  return `images/${imageFileBasename(image)}`;
}

function letterboxRect(
  iw: number,
  ih: number,
  cw: number,
  ch: number
): { dx: number; dy: number; dw: number; dh: number } {
  if (!iw || !ih) return { dx: 0, dy: 0, dw: cw, dh: ch };
  const scale = Math.min(cw / iw, ch / ih);
  const dw = iw * scale;
  const dh = ih * scale;
  const dx = (cw - dw) / 2;
  const dy = (ch - dh) / 2;
  return { dx, dy, dw, dh };
}

const DEFAULT_SUBTITLE_SETTINGS: SubtitleSettings = {
  font_size: 48,
  position: 'bottom',
  positionY: 88,
  color: '#FFFFFF',
  highlight_color: '#FFFF00',
  highlight_enabled: true,
  outline_width: 4,
  subtitle_offset_ms: 0,
};

export default function EditorPage() {
  const params = useParams();
  const router = useRouter();
  const id = projectIdFromParams(params.id);
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
  const [exportSubtitleQcWarnings, setExportSubtitleQcWarnings] = useState<string[] | null>(null);
  const [dataLoaded, setDataLoaded] = useState(false);
  const [subtitlesPanelOpen, setSubtitlesPanelOpen] = useState(true);
  const [transitionsPanelOpen, setTransitionsPanelOpen] = useState(true);
  const [globalFadeDuration, setGlobalFadeDuration] = useState(0.5);
  const [timelineZoom, setTimelineZoom] = useState(1);
  const [zoomStep, setZoomStep] = useState(0.5);
  const [playbackSpeed, setPlaybackSpeed] = useState(1);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number | null>(null);
  const currentTimeRef = useRef<number>(0);
  const autoSubtitlesInitRef = useRef(false);
  const timelineInnerRef = useRef<HTMLDivElement>(null);
  const timelineOuterRef = useRef<HTMLDivElement>(null);
  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dragRef = useRef<{ startX: number; startStart: number; startEnd: number; segmentId: number; side: 'left' | 'right' } | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const replaceInputRef = useRef<HTMLInputElement>(null);
  const addInputRef = useRef<HTMLInputElement>(null);
  /** Synchronous target for replace; must be set in the same user gesture as file input .click() (avoids null + browser picker quirks). */
  const replaceTargetSegmentIdRef = useRef<number | null>(null);
  const [replacingSegmentId, setReplacingSegmentId] = useState<number | null>(null);
  const [selectedSegmentId, setSelectedSegmentId] = useState<number | null>(null);
  const [movingSegmentId, setMovingSegmentId] = useState<number | null>(null);
  const [isSnapping, setIsSnapping] = useState(false);
  const [parkedSegments, setParkedSegments] = useState<Segment[]>([]);
  /** Full snapshot for PUT /editor-state (must match what we load & export or keys get stuck on disk). */
  const persistStateRef = useRef({
    segments,
    parkedSegments,
    subtitles,
    transitions,
    subtitleSettings,
    has_subtitles: hasSubtitles,
    font_size: subtitleSettings.font_size,
    alignment: subtitleSettings.position,
    subtitle_offset_ms: subtitleSettings.subtitle_offset_ms ?? 0,
    totalDuration,
    end_padding_seconds: 1.5,
    canvas_display_width: 1920 as number,
  });
  persistStateRef.current = {
    segments,
    parkedSegments,
    subtitles,
    transitions,
    subtitleSettings,
    has_subtitles: hasSubtitles,
    font_size: subtitleSettings.font_size,
    alignment: subtitleSettings.position,
    subtitle_offset_ms: subtitleSettings.subtitle_offset_ms ?? 0,
    totalDuration,
    end_padding_seconds: 1.5,
    canvas_display_width: canvasRef.current?.offsetWidth ?? 1920,
  };

  type AssemblyQc = {
    pass: boolean;
    match_pct: number;
    matched: number;
    total: number;
    issues: string[];
    problem_scenes: number[];
    avg_duration_sec: number;
    max_duration_sec: number;
  };
  const [assemblyQc, setAssemblyQc] = useState<AssemblyQc | null>(null);
  const [assemblyQcOpen, setAssemblyQcOpen] = useState(false);

  const runValidateAssembly = useCallback(async () => {
    if (!id) return;
    try {
      const apiBase = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8050';
      const res = await fetch(`${apiBase}/api/projects/${encodeProjectIdForPath(id)}/validate-assembly`);
      if (!res.ok) return;
      const data = await res.json() as AssemblyQc;
      setAssemblyQc(data);
      // Auto-open panel when there are problems
      if (!data.pass) setAssemblyQcOpen(true);
    } catch {
      // validation is advisory — silent failure is OK
    }
  }, [id]);

  const [resyncPending, setResyncPending] = useState(false);

  // resyncPending is set to true while the re-assembly API call is in flight

  const [subtitleGenPending, setSubtitleGenPending] = useState(false);
  const subtitleGenLockRef = useRef(false);
  const hasSubtitlesRef = useRef(hasSubtitles);
  const subtitlesListRef = useRef<SubtitleLine[]>(subtitles);
  hasSubtitlesRef.current = hasSubtitles;
  subtitlesListRef.current = subtitles;

  const buildPersistPayload = useCallback(
    (overrideSubtitles: SubtitleLine[], overrideHasSubtitles: boolean) => ({
      segments,
      parkedSegments,
      subtitles: overrideSubtitles,
      transitions,
      subtitleSettings,
      has_subtitles: overrideHasSubtitles,
      font_size: subtitleSettings.font_size,
      alignment: subtitleSettings.position,
      subtitle_offset_ms: subtitleSettings.subtitle_offset_ms ?? 0,
      totalDuration,
      end_padding_seconds: 1.5,
      canvas_display_width: canvasRef.current?.offsetWidth ?? 1920,
    }),
    [segments, parkedSegments, transitions, subtitleSettings, totalDuration],
  );

  const runGenerateSubtitles = useCallback(async () => {
    if (!id || subtitleGenLockRef.current) return;
    subtitleGenLockRef.current = true;
    setSubtitleGenPending(true);
    if (autoSaveTimerRef.current) {
      clearTimeout(autoSaveTimerRef.current);
      autoSaveTimerRef.current = null;
    }
    const hadSubs = hasSubtitlesRef.current;
    const backupSubs = hadSubs ? [...subtitlesListRef.current] : [];
    if (hadSubs) {
      setSubtitles([]);
    }
    try {
      const result = (await generateSubtitles(id)) as SubtitleLine[];
      if (!Array.isArray(result)) {
        showToast('Invalid subtitle response', 'error');
        if (hadSubs) setSubtitles(backupSubs);
        return;
      }
      const snapped = snapSubtitleLines(result);
      await saveEditorState(id, buildPersistPayload(snapped, true));
      setSubtitles(snapped);
      setHasSubtitles(true);
    } catch (err) {
      if (hadSubs) setSubtitles(backupSubs);
      showToast(err instanceof Error ? err.message : 'Request failed', 'error');
    } finally {
      subtitleGenLockRef.current = false;
      setSubtitleGenPending(false);
    }
  }, [id, buildPersistPayload, showToast]);

  const [history, setHistory] = useState<HistorySnapshot[]>([]);
  const [historyIndex, setHistoryIndex] = useState<number>(-1);
  const historyRef = useRef<HistorySnapshot[]>([]);
  const historyIndexRef = useRef<number>(-1);
  useEffect(() => {
    historyRef.current = history;
  }, [history]);
  useEffect(() => {
    historyIndexRef.current = historyIndex;
  }, [historyIndex]);
  useEffect(() => {
    currentTimeRef.current = currentTime;
  }, [currentTime]);

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

        const rawSubs = editorState.subtitles;
        const stateSubtitles: SubtitleLine[] = Array.isArray(rawSubs) ? rawSubs : [];
        if (stateSubtitles.length > 0) {
          setSubtitles(snapSubtitleLines(stateSubtitles));
          setHasSubtitles(true);
        } else {
          setSubtitles([]);
          setHasSubtitles(false);
        }
        const stateTransitions = editorState.transitions as Record<string, Transition> | undefined;
        if (stateTransitions && Object.keys(stateTransitions).length > 0) {
          setTransitions(stateTransitions);
        }
        const stateSettings = editorState.subtitleSettings as Partial<SubtitleSettings> | undefined;
        const rawOff = editorState.subtitle_offset_ms;
        const hasStoredOffset = typeof rawOff === 'number' && Number.isFinite(rawOff);
        if (
          (stateSettings && (stateSettings.font_size != null || stateSettings.position != null || stateSettings.color != null)) ||
          hasStoredOffset
        ) {
          const merged: SubtitleSettings = { ...DEFAULT_SUBTITLE_SETTINGS, ...stateSettings };
          if (hasStoredOffset) merged.subtitle_offset_ms = Math.round(rawOff as number);
          setSubtitleSettings(merged);
        }

        const duration = finalSegments.length > 0
          ? Math.max(...finalSegments.map((seg) => seg.end)) + 1.5
          : 0;
        setTotalDuration(duration);
        const initialSnapshot: HistorySnapshot = {
          segments: JSON.parse(JSON.stringify(finalSegments)),
          parkedSegments: JSON.parse(JSON.stringify((editorState.parkedSegments as Segment[] | undefined) || [])),
          transitions: JSON.parse(JSON.stringify((editorState.transitions as Record<string, Transition> | undefined) || {})),
        };
        setHistory([initialSnapshot]);
        setHistoryIndex(0);
      } catch (err) {
        showToast(err instanceof Error ? err.message : 'Request failed', 'error');
      } finally {
        setDataLoaded(true);
      }
    };
    load();
  }, [id, showToast]);

  // Auto-run assembly validation whenever the editor loads or segments change.
  useEffect(() => {
    if (!dataLoaded || segments.length === 0) return;
    void runValidateAssembly();
  }, [dataLoaded, runValidateAssembly]);

  useEffect(() => {
    if (!dataLoaded || !id || autoSubtitlesInitRef.current) return;
    if (segments.length === 0 || hasSubtitles || subtitleGenPending) return;
    autoSubtitlesInitRef.current = true;
    runGenerateSubtitles().catch(() => {
      autoSubtitlesInitRef.current = false;
    });
  }, [
    dataLoaded,
    id,
    segments.length,
    hasSubtitles,
    subtitleGenPending,
    runGenerateSubtitles,
  ]);

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
      const path = imagePathForFileApi(seg.image);
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

    const cw = canvas.width;
    const ch = canvas.height;

    const activeSeg = segments.find((s) => s.start <= time && time < s.end);
    const lastSegment = segments.length > 0 ? segments[segments.length - 1] : null;
    const isTailZone = !!lastSegment && time >= lastSegment.end && time <= totalDuration;
    const segmentToRender = activeSeg ?? (isTailZone ? lastSegment : null);
    if (segmentToRender) {
      const img = preloadedImages[segmentToRender.segment_id.toString()];
      if (img && img.complete && img.naturalWidth > 0) {
        ctx.fillStyle = '#000000';
        ctx.fillRect(0, 0, cw, ch);
        const r0 = letterboxRect(img.naturalWidth, img.naturalHeight, cw, ch);
        ctx.drawImage(img, r0.dx, r0.dy, r0.dw, r0.dh);
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
              if (nextImg && nextImg.complete && nextImg.naturalWidth > 0) {
                ctx.globalAlpha = alpha;
                const r1 = letterboxRect(nextImg.naturalWidth, nextImg.naturalHeight, cw, ch);
                ctx.drawImage(nextImg, r1.dx, r1.dy, r1.dw, r1.dh);
                ctx.globalAlpha = 1.0;
              }
            }
          }
        }
      } else {
        ctx.fillStyle = '#000000';
        ctx.fillRect(0, 0, cw, ch);
      }
    } else {
      ctx.fillStyle = '#000000';
      ctx.fillRect(0, 0, cw, ch);
    }

    const subTime = time - (subtitleSettings.subtitle_offset_ms ?? 0) / 1000;
    const activeLine = subtitles.find((l) => l.start <= subTime && subTime < l.end);
    if (activeLine) {
      const scaledFontSize = Math.round((subtitleSettings.font_size || 48) * (canvas.width / EXPORT_W));
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
          ctx.lineWidth = outlineWidth * (canvas.width / EXPORT_W);
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
          const isActive = w.start <= subTime && subTime < w.end;
          if (outlineWidth > 0) {
            ctx.strokeStyle = '#000000';
            ctx.lineWidth = outlineWidth * (canvas.width / EXPORT_W);
            ctx.strokeText(wordWithSpace, x, y);
          }
          ctx.fillStyle = isActive ? highlightColor : baseColor;
          ctx.fillText(wordWithSpace, x, y);
          x += ctx.measureText(wordWithSpace).width;
        });
        ctx.textAlign = 'center';
      }
    }
  }, [segments, subtitles, subtitleSettings, preloadedImages, transitions, totalDuration]);

  useEffect(() => {
    renderFrame(currentTime);
  }, [currentTime, segments, subtitles, renderFrame]);

  useEffect(() => {
    const audioEl = audioRef.current;
    if (!isPlaying) {
      if (rafRef.current != null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      if (audioEl) {
        audioEl.pause();
      }
      return;
    }
    if (audioEl) {
      audioEl.currentTime = currentTimeRef.current;
      audioEl.playbackRate = playbackSpeed;
      void audioEl.play();
    }
    const loop = () => {
      const el = audioRef.current;
      if (el && !el.paused) {
        const cap =
          totalDuration > 0 ? totalDuration : (Number.isFinite(el.duration) && el.duration > 0 ? el.duration : Infinity);
        const t = Math.min(Math.max(0, el.currentTime), cap);
        setCurrentTime(t);
        if (totalDuration > 0 && t >= totalDuration - 0.02) {
          setIsPlaying(false);
          setCurrentTime(totalDuration);
        }
      }
      rafRef.current = requestAnimationFrame(loop);
    };
    rafRef.current = requestAnimationFrame(loop);
    return () => {
      if (rafRef.current != null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      if (audioEl) {
        audioEl.pause();
      }
    };
  }, [isPlaying, totalDuration, playbackSpeed]);

  useEffect(() => {
    const audioEl = audioRef.current;
    if (!audioEl) return;
    const onEnded = () => {
      setIsPlaying(false);
      const dur = Number.isFinite(audioEl.duration) ? audioEl.duration : 0;
      setCurrentTime((prev) =>
        totalDuration > 0 ? Math.min(totalDuration, Math.max(prev, dur)) : dur,
      );
    };
    audioEl.addEventListener('ended', onEnded);
    return () => audioEl.removeEventListener('ended', onEnded);
  }, [totalDuration]);

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

  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.width = EXPORT_W;
    canvas.height = EXPORT_H;
  }, [dataLoaded]);

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

  const updateTotalDuration = useCallback((nextSegments?: Segment[]) => {
    const source = nextSegments ?? segments;
    const maxEnd = source.length > 0
      ? Math.max(...source.map((seg) => seg.end))
      : 0;
    setTotalDuration(maxEnd + 1.5);
  }, [segments]);

  const buildSnapshot = useCallback((): HistorySnapshot => {
    return {
      segments: JSON.parse(JSON.stringify(segments)),
      parkedSegments: JSON.parse(JSON.stringify(parkedSegments)),
      transitions: JSON.parse(JSON.stringify(transitions)),
    };
  }, [segments, parkedSegments, transitions]);

  const applySnapshot = useCallback((snapshot: HistorySnapshot) => {
    setSegments(snapshot.segments || []);
    setParkedSegments(snapshot.parkedSegments || []);
    setTransitions(snapshot.transitions || {});
    updateTotalDuration(snapshot.segments || []);
  }, [updateTotalDuration]);

  // Undo/Redo helpers
  const saveToHistory = useCallback((snapshot?: HistorySnapshot) => {
    const currentState = snapshot ?? buildSnapshot();
    setHistory((prev) => {
      const newHistory = prev.slice(0, historyIndexRef.current + 1);
      const last = newHistory[newHistory.length - 1];
      if (last && JSON.stringify(last) === JSON.stringify(currentState)) {
        return prev;
      }
      newHistory.push(currentState);
      historyRef.current = newHistory;
      historyIndexRef.current = newHistory.length - 1;
      setHistoryIndex(newHistory.length - 1);
      return newHistory;
    });
  }, [buildSnapshot]);

  const performUndo = useCallback(() => {
    const idx = historyIndexRef.current;
    if (idx <= 0) return;
    const previousState = historyRef.current[idx - 1];
    if (!previousState) return;
    applySnapshot(previousState);
    historyIndexRef.current = idx - 1;
    setHistoryIndex(idx - 1);
  }, [applySnapshot]);

  const performRedo = useCallback(() => {
    const idx = historyIndexRef.current;
    if (idx >= historyRef.current.length - 1) return;
    const nextState = historyRef.current[idx + 1];
    if (!nextState) return;
    applySnapshot(nextState);
    historyIndexRef.current = idx + 1;
    setHistoryIndex(idx + 1);
  }, [applySnapshot]);

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

  const runResyncImages = useCallback(async () => {
    if (!id || resyncPending) return;
    setResyncPending(true);
    try {
      const apiBase = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8050';
      const res = await fetch(
        `${apiBase}/api/projects/${encodeProjectIdForPath(id)}/assemble`,
        { method: 'POST' },
      );
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({ detail: 'Assembly failed' }));
        showToast((errBody as { detail?: string }).detail ?? 'Assembly failed', 'error');
        return;
      }
      const newSegs = (await res.json()) as Segment[];
      if (Array.isArray(newSegs) && newSegs.length > 0) {
        saveToHistory();
        setSegments(newSegs);
        updateTotalDuration(newSegs);
        showToast('Images re-synced to audio ✓', 'success');
        void runValidateAssembly();
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Re-sync failed', 'error');
    } finally {
      setResyncPending(false);
    }
  }, [id, resyncPending, showToast, saveToHistory, updateTotalDuration, runValidateAssembly]);

  const autoSave = useCallback(() => {
    if (!id) return;
    if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    autoSaveTimerRef.current = setTimeout(() => {
      autoSaveTimerRef.current = null;
      saveEditorState(id, persistStateRef.current);
    }, 1000);
  }, [id]);

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

  const handleBack = () => router.push(`/project/${encodeProjectIdForRoute(id)}`);

  const buildEditorState = () => ({ ...persistStateRef.current });

  const handleExportClick = async () => {
    if (!id) return;
    setShowExportOverlay(true);
    setExportError(null);
    setExportDone(false);
    setExportSubtitleQcWarnings(null);
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
        const s = await getJobStatus(exportJobId) as {
          status?: string;
          percent?: number;
          error?: string;
          subtitle_qc_warnings?: string[];
        };
        if (s?.status === 'done') {
          setExportDone(true);
          setExportStatus('Done');
          const w = s?.subtitle_qc_warnings;
          setExportSubtitleQcWarnings(Array.isArray(w) && w.length > 0 ? w : null);
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
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: 'var(--garden-surface)' }}>
        <p className="text-[color:var(--garden-text-soft)]">Loading…</p>
      </div>
    );
  }

  if (dataLoaded && segments.length === 0) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4" style={{ backgroundColor: 'var(--garden-surface)' }}>
        <p className="text-[color:var(--garden-text-soft)]">No segments — go back and run Assemble</p>
        <button
          type="button"
          onClick={handleBack}
          className="rounded px-4 py-2 text-sm font-medium text-[color:var(--garden-cream)]"
          style={{ backgroundColor: 'var(--garden-accent)' }}
        >
          Back
        </button>
      </div>
    );
  }

  if (dataLoaded && totalDuration === 0 && segments.length > 0) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4" style={{ backgroundColor: 'var(--garden-surface)' }}>
        <p className="text-[color:var(--garden-text-soft)]">No audio — go back and process audio</p>
        <button
          type="button"
          onClick={handleBack}
          className="rounded px-4 py-2 text-sm font-medium text-[color:var(--garden-cream)]"
          style={{ backgroundColor: 'var(--garden-accent)' }}
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
        style={{ backgroundColor: 'var(--garden-card)' }}
      >
        <button
          type="button"
          onClick={handleBack}
          className="mr-3 text-[color:var(--garden-text-soft)] hover:opacity-90"
          aria-label="Back"
        >
          ←
        </button>
        <span className="text-[color:var(--garden-text)] flex-1">Editor — {project.title}</span>
        <button
          type="button"
          onClick={handleExportClick}
          className="text-[color:var(--garden-text-soft)] hover:opacity-90 text-sm"
        >
          Export
        </button>
      </header>

      <main className="pt-14" style={{ backgroundColor: 'var(--garden-surface)' }}>
        <div className="flex justify-center p-4">
          <input
            ref={replaceInputRef}
            type="file"
            accept="image/png,image/jpeg,image/jfif,.jfif"
            style={{ display: 'none' }}
            onChange={async (e) => {
              const file = e.target.files?.[0];
              const targetId = replaceTargetSegmentIdRef.current;
              replaceTargetSegmentIdRef.current = null;
              if (!file) return;
              if (targetId === null) {
                if (replaceInputRef.current) replaceInputRef.current.value = '';
                return;
              }
              const seg =
                segments.find(s => s.segment_id === targetId) ??
                parkedSegments.find(s => s.segment_id === targetId);
              if (!seg) {
                showToast('Could not find the selected scene for replace.', 'error');
                if (replaceInputRef.current) replaceInputRef.current.value = '';
                return;
              }
              const imageName = imageFileBasename(seg.image);
              const formData = new FormData();
              formData.append('file', file);
              const apiBase = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8050';
              let res: Response;
              try {
                res = await fetch(
                  `${apiBase}/api/projects/${encodeProjectIdForPath(id)}/replace-image/${encodeURIComponent(imageName)}`,
                  { method: 'POST', body: formData }
                );
              } catch (err) {
                showToast(err instanceof Error ? err.message : 'Network error while replacing image', 'error');
                if (replaceInputRef.current) replaceInputRef.current.value = '';
                return;
              }
              if (!res.ok) {
                const errData = (await res.json().catch(() => ({}))) as { detail?: string };
                showToast(
                  typeof errData.detail === 'string' ? errData.detail : `Replace failed (${res.status})`,
                  'error'
                );
                if (replaceInputRef.current) replaceInputRef.current.value = '';
                return;
              }
              const pathForUrl = imagePathForFileApi(imageName);
              const newUrl = `${fileUrl(id, pathForUrl)}?t=${Date.now()}`;
              saveToHistory();
              setSegments(prev =>
                prev.map(s =>
                  s.segment_id === targetId ? { ...s, image: imageName } : s
                )
              );
              setParkedSegments(prev =>
                prev.map(s => (s.segment_id === targetId ? { ...s, image: imageName } : s))
              );
              updateTotalDuration();
              const img = new Image();
              img.src = newUrl;
              img.onload = () => {
                setPreloadedImages(prev => ({ ...prev, [targetId.toString()]: img }));
              };
              setReplacingSegmentId(null);
              if (replaceInputRef.current) replaceInputRef.current.value = '';
              autoSave();
            }}
          />
          <input
            ref={addInputRef}
            type="file"
            accept="image/png,image/jpeg,image/jfif,.jfif"
            style={{ display: 'none' }}
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file || selectedSegmentId === null) return;
              const idx = segments.findIndex(s => s.segment_id === selectedSegmentId);
              if (idx === -1) return;
              const newId = Date.now();
              const dot = file.name.lastIndexOf('.');
              const rawExt = dot >= 0 ? file.name.slice(dot).toLowerCase() : '';
              const ext =
                rawExt === '.png' || rawExt === '.jpg' || rawExt === '.jpeg' || rawExt === '.jfif' ? rawExt : '.png';
              const newImageName = `${newId}${ext}`;
              const formData = new FormData();
              formData.append('file', file);
              const apiBase = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8050';
              await fetch(`${apiBase}/api/projects/${encodeProjectIdForPath(id)}/replace-image/${encodeURIComponent(newImageName)}`, {
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
              const newUrl = `${fileUrl(id, `images/${newImageName}`)}?t=${Date.now()}`;
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
              className="px-4 pb-1 py-2 flex items-center gap-2 flex-wrap rounded-md border border-[color:var(--garden-border)]"
              style={{ minHeight: '24px', backgroundColor: 'var(--garden-well)' }}
            >
              <span
                className="text-lg font-medium px-3 py-1 rounded-md"
                style={{ backgroundColor: 'var(--garden-card)', color: 'var(--garden-accent)' }}
              >
                Scene {sceneIdx + 1}
              </span>
              {activeSeg.text ? (
                <span className="text-base" style={{ color: 'var(--garden-text)', opacity: 0.85 }}>
                  {activeSeg.text}
                </span>
              ) : null}
              {activeSeg.matched === false && (
                <span
                  className="text-xs px-1.5 py-0.5 rounded"
                  style={{ backgroundColor: 'var(--garden-unmatched-border)', color: 'var(--garden-cream)' }}
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
            className="rounded px-4 py-2 text-sm font-medium text-[color:var(--garden-cream)]"
            style={{ backgroundColor: 'var(--garden-accent)' }}
          >
            {isPlaying ? 'Pause' : 'Play'}
          </button>
          <span className="text-[color:var(--garden-text)] font-mono text-sm">
            {formatTime(currentTime)}
          </span>
          <select
            value={playbackSpeed}
            onChange={e => setPlaybackSpeed(Number(e.target.value))}
            className="rounded bg-[var(--garden-surface)] border border-[color:var(--garden-border)] text-[color:var(--garden-text)] text-xs px-2 py-1 ml-2"
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
            className="flex-1 min-w-[120px] accent-[color:var(--garden-accent)]"
          />
        </div>

        <div className="mx-4 mt-4 flex gap-4">
          <div className="flex-[0.6] flex flex-col">
            <div className="flex items-center justify-between mb-3 gap-2 flex-wrap">
              <span className="text-[color:var(--garden-text)] font-semibold">Subtitles</span>
              <div className="flex items-center gap-2">
                {hasSubtitles && (
                  <>
                    <button
                      type="button"
                      disabled={subtitleGenPending}
                      onClick={() => void runGenerateSubtitles()}
                      className="text-[color:var(--garden-text-soft)] hover:text-[color:var(--garden-text)] text-xs px-2 py-1 rounded border border-[color:var(--garden-border)] hover:border-[color:var(--garden-accent)] transition-colors disabled:opacity-50"
                    >
                      {subtitleGenPending ? 'Regenerating…' : 'Regenerate from audio'}
                    </button>
                    <button
                      type="button"
                      disabled={subtitleGenPending}
                      onClick={async () => {
                        if (!id) return;
                        if (autoSaveTimerRef.current) {
                          clearTimeout(autoSaveTimerRef.current);
                          autoSaveTimerRef.current = null;
                        }
                        const payload = {
                          segments,
                          parkedSegments,
                          subtitles: [] as SubtitleLine[],
                          transitions,
                          subtitleSettings,
                          has_subtitles: false,
                          font_size: subtitleSettings.font_size,
                          alignment: subtitleSettings.position,
                          subtitle_offset_ms: subtitleSettings.subtitle_offset_ms ?? 0,
                          totalDuration,
                          end_padding_seconds: 1.5,
                          canvas_display_width: canvasRef.current?.offsetWidth ?? 1920,
                        };
                        try {
                          await saveEditorState(id, payload);
                          setSubtitles([]);
                          setHasSubtitles(false);
                          showToast('All subtitles removed', 'success');
                        } catch (err) {
                          showToast(err instanceof Error ? err.message : 'Could not save', 'error');
                        }
                      }}
                      className="text-[color:var(--garden-text-soft)] hover:text-red-400 text-xs px-2 py-1 rounded border border-[color:var(--garden-border)] hover:border-red-400/60 transition-colors disabled:opacity-50"
                    >
                      Remove all subtitles
                    </button>
                  </>
                )}
                <button
                  type="button"
                  onClick={() => setSubtitlesPanelOpen(o => !o)}
                  className="text-[color:var(--garden-text-soft)] hover:text-[color:var(--garden-text)] text-xs px-2 py-1 rounded border border-[color:var(--garden-border)] hover:border-[color:var(--garden-accent)] transition-colors"
                >
                  {subtitlesPanelOpen ? '▲ Hide' : '▼ Show'}
                </button>
              </div>
            </div>
            {subtitlesPanelOpen && (
            <>
            {!hasSubtitles ? (
              <div className="flex flex-col gap-2">
                <button
                  type="button"
                  disabled={subtitleGenPending}
                  onClick={() => void runGenerateSubtitles()}
                  className="rounded px-4 py-2 text-sm font-medium text-[color:var(--garden-cream)] w-fit disabled:opacity-60"
                  style={{ backgroundColor: 'var(--garden-accent)' }}
                >
                  {subtitleGenPending ? 'Generating from transcript…' : 'Generate Subtitles'}
                </button>
                {subtitleGenPending && (
                  <p className="text-xs text-[color:var(--garden-text-soft)]">
                    Building fresh captions from your audio file (not from an old save).
                  </p>
                )}
              </div>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-3 mb-2">
                  <label className="flex items-center gap-2">
                    <span className="text-[color:var(--garden-text-soft)] text-sm">Font Size</span>
                    <input
                      type="range"
                      min={24}
                      max={72}
                      value={subtitleSettings.font_size}
                      onChange={(e) => {
                        setSubtitleSettings((s) => ({ ...s, font_size: Number(e.target.value) }));
                        autoSave();
                      }}
                      className="accent-[color:var(--garden-accent)]"
                    />
                    <span className="text-[color:var(--garden-text-soft)] text-xs font-mono w-6">{subtitleSettings.font_size}</span>
                  </label>
                  <label className="flex items-center gap-2">
                    <span className="text-[color:var(--garden-text-soft)] text-xs">Y Position %</span>
                    <input
                      type="number"
                      min={0}
                      max={100}
                      value={subtitleSettings.positionY ?? 88}
                      onChange={(e) => {
                        setSubtitleSettings((prev) => ({ ...prev, positionY: Number(e.target.value) }));
                        autoSave();
                      }}
                      className="w-16 bg-[var(--garden-surface)] border border-[color:var(--garden-border)] rounded text-[color:var(--garden-text)] text-xs px-2 py-1"
                    />
                  </label>
                  <label className="flex items-center gap-2">
                    <span className="text-[color:var(--garden-text-soft)] text-sm">Color</span>
                    <input
                      type="color"
                      value={subtitleSettings.color}
                      onChange={(e) => {
                        setSubtitleSettings((s) => ({ ...s, color: e.target.value }));
                        autoSave();
                      }}
                      className="w-8 h-8 rounded border border-[color:var(--garden-border)] cursor-pointer"
                    />
                  </label>
                  <label className="flex items-center gap-2">
                    <span className="text-[color:var(--garden-text-soft)] text-sm">Highlight</span>
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
                    <span className="text-[color:var(--garden-text-soft)] text-sm">Outline</span>
                    <input
                      type="range"
                      min={0}
                      max={12}
                      step={1}
                      value={subtitleSettings.outline_width ?? 4}
                      onChange={e => setSubtitleSettings(s => ({...s, outline_width: Number(e.target.value)}))}
                      className="w-24 accent-[color:var(--garden-accent)]"
                    />
                    <span className="text-[color:var(--garden-step-muted)] text-xs">{subtitleSettings.outline_width ?? 4}px</span>
                  </label>
                  <label className="flex items-center gap-2 flex-wrap">
                    <span className="text-[color:var(--garden-text-soft)] text-xs whitespace-nowrap" title="Shifts all cues vs video/audio for export and preview">
                      Timing offset (ms)
                    </span>
                    <input
                      type="number"
                      min={-3000}
                      max={3000}
                      step={10}
                      value={subtitleSettings.subtitle_offset_ms ?? 0}
                      onChange={(e) => {
                        const n = Number(e.target.value);
                        setSubtitleSettings((s) => ({
                          ...s,
                          subtitle_offset_ms: Number.isFinite(n) ? Math.round(n) : 0,
                        }));
                        autoSave();
                      }}
                      className="w-20 bg-[var(--garden-surface)] border border-[color:var(--garden-border)] rounded text-[color:var(--garden-text)] text-xs px-2 py-1"
                    />
                  </label>
                </div>
                <div className="max-h-64 overflow-y-auto space-y-1">
                  {subtitles.map((line, idx) => (
                    <div
                      key={idx}
                      className="rounded p-2 flex gap-3 cursor-pointer hover:bg-[var(--garden-surface)]"
                      onClick={() => setCurrentTime(line.start)}
                    >
                      <span className="text-[color:var(--garden-text-soft)] text-xs font-mono flex-shrink-0">
                        {formatTime(line.start)}
                      </span>
                      <span
                        contentEditable
                        suppressContentEditableWarning
                        className="text-[color:var(--garden-text)] flex-1 outline-none"
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
              <span className="text-[color:var(--garden-text)] font-semibold">Transitions</span>
              <button
                onClick={() => setTransitionsPanelOpen(o => !o)}
                className="text-[color:var(--garden-text-soft)] hover:text-[color:var(--garden-text)] text-xs px-2 py-1 rounded border border-[color:var(--garden-border)] hover:border-[color:var(--garden-accent)] transition-colors"
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
                className="rounded bg-[var(--garden-surface)] border border-[color:var(--garden-border)] text-[color:var(--garden-text)] text-xs px-2 py-1"
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
                className="rounded border border-[color:var(--garden-accent)] text-[color:var(--garden-accent)] text-xs px-2 py-1 hover:bg-[color-mix(in_srgb,var(--garden-accent)_12%,transparent)]"
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
                className="rounded border border-[color:var(--garden-danger)] text-[color:var(--garden-danger)] text-xs px-2 py-1 hover:bg-[color-mix(in_srgb,var(--garden-danger)_12%,transparent)]"
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
                      <span className="text-[color:var(--garden-text-soft)] text-xs">
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
                        className="rounded bg-[var(--garden-surface)] border border-[color:var(--garden-border)] text-[color:var(--garden-text)] text-sm px-2 py-1"
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
                          className="accent-[color:var(--garden-accent)]"
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
        {/* ── Assembly Quality Report ─────────────────────────────────── */}
        {assemblyQc && (
          <div className="mx-4 mb-2 rounded-lg border text-xs"
            style={{
              borderColor: assemblyQc.pass ? 'var(--garden-border)' : 'var(--garden-warning)',
              backgroundColor: assemblyQc.pass ? 'var(--garden-surface)' : 'rgba(255,180,0,0.07)',
            }}
          >
            <div
              className="flex items-center gap-2 px-3 py-2 cursor-pointer select-none"
              onClick={() => setAssemblyQcOpen(o => !o)}
            >
              <span style={{ color: assemblyQc.pass ? 'var(--garden-accent)' : 'var(--garden-warning)' }}>
                {assemblyQc.pass ? '✓' : '⚠'}
              </span>
              <span className="font-semibold" style={{ color: 'var(--garden-text)' }}>
                Sync Quality: {assemblyQc.match_pct}% matched ({assemblyQc.matched}/{assemblyQc.total} scenes)
              </span>
              {assemblyQc.problem_scenes.length > 0 && (
                <span className="ml-1" style={{ color: 'var(--garden-warning)' }}>
                  — {assemblyQc.problem_scenes.length} problem scene{assemblyQc.problem_scenes.length > 1 ? 's' : ''}
                </span>
              )}
              <div className="ml-auto flex items-center gap-1">
                <button
                  type="button"
                  disabled={resyncPending}
                  onClick={(e) => { e.stopPropagation(); void runResyncImages(); }}
                  title="Re-run alignment and update all image positions from audio"
                  className="text-xs px-2 py-0.5 rounded border disabled:opacity-50"
                  style={{ borderColor: 'var(--garden-accent)', color: 'var(--garden-accent)', background: 'transparent' }}
                >
                  {resyncPending ? 'Re-syncing…' : '↺ Re-sync Images'}
                </button>
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); void runValidateAssembly(); }}
                  className="text-xs px-2 py-0.5 rounded border border-[color:var(--garden-border)] hover:border-[color:var(--garden-accent)] text-[color:var(--garden-text-soft)] hover:text-[color:var(--garden-text)]"
                >
                  Re-check
                </button>
              </div>
              <span style={{ color: 'var(--garden-text-soft)' }}>{assemblyQcOpen ? '▲' : '▼'}</span>
            </div>
            {assemblyQcOpen && (
              <div className="px-3 pb-3 flex flex-col gap-1" style={{ color: 'var(--garden-text-soft)' }}>
                <div>Avg scene duration: {assemblyQc.avg_duration_sec}s · Longest: {assemblyQc.max_duration_sec}s</div>
                {assemblyQc.issues.map((iss, i) => (
                  <div key={i} style={{ color: 'var(--garden-warning)' }}>• {iss}</div>
                ))}
                {assemblyQc.problem_scenes.length > 0 && (
                  <div>Problem scene IDs: {assemblyQc.problem_scenes.join(', ')}</div>
                )}
                {assemblyQc.pass && <div style={{ color: 'var(--garden-accent)' }}>All checks passed — assembly looks correct.</div>}
              </div>
            )}
          </div>
        )}

        <div className="mt-4 mx-4 rounded-xl" style={{ backgroundColor: 'var(--garden-timeline)' }}>
          <div className="flex items-center gap-2 px-2 py-1">
            <span className="text-[color:var(--garden-text-soft)] text-xs">Zoom</span>
            <button
              onClick={() => setTimelineZoom(z => Math.max(1, parseFloat((z - zoomStep).toFixed(1))))}
              className="text-[color:var(--garden-text-soft)] hover:text-[color:var(--garden-text)] text-xs px-2 py-0.5 rounded border border-[color:var(--garden-border)] hover:border-[color:var(--garden-accent)]"
            >−</button>
            <span className="text-[color:var(--garden-text-soft)] text-xs w-8 text-center">{timelineZoom}x</span>
            <button
              onClick={() => setTimelineZoom(z => Math.min(100, parseFloat((z + zoomStep).toFixed(1))))}
              className="text-[color:var(--garden-text-soft)] hover:text-[color:var(--garden-text)] text-xs px-2 py-0.5 rounded border border-[color:var(--garden-border)] hover:border-[color:var(--garden-accent)]"
            >+</button>
            <button
              onClick={() => setTimelineZoom(1)}
              className="text-[color:var(--garden-text-soft)] hover:text-[color:var(--garden-text)] text-xs px-2 py-0.5 rounded border border-[color:var(--garden-border)] hover:border-[color:var(--garden-accent)]"
            >Reset</button>
            <input
              type="range"
              min={1}
              max={100}
              step={0.1}
              value={timelineZoom}
              onChange={e => setTimelineZoom(Number(e.target.value))}
              className="w-32 accent-[color:var(--garden-accent)]"
            />
            <select
              value={zoomStep}
              onChange={e => setZoomStep(Number(e.target.value))}
              className="rounded bg-[var(--garden-surface)] border border-[color:var(--garden-border)] text-[color:var(--garden-text)] text-xs px-1 py-0.5"
            >
              <option value={0.5}>0.5x step</option>
              <option value={1}>1x step</option>
              <option value={2}>2x step</option>
              <option value={3}>3x step</option>
            </select>
          </div>
          {selectedSegmentId !== null && (
            <div className="flex items-center gap-2 px-2 py-1 border-b border-[color:var(--garden-border)]" style={{ backgroundColor: 'var(--garden-card)' }}>
              <span className="text-[color:var(--garden-text-soft)] text-xs">Scene {(segments.findIndex(s => s.segment_id === selectedSegmentId) + 1)} selected</span>
              <button
                onClick={() => {
                  if (selectedSegmentId === null) return;
                  replaceTargetSegmentIdRef.current = selectedSegmentId;
                  setReplacingSegmentId(selectedSegmentId);
                  replaceInputRef.current?.click();
                }}
                className="text-xs px-2 py-0.5 rounded border border-[color:var(--garden-accent)] text-[color:var(--garden-accent)] hover:bg-[color-mix(in_srgb,var(--garden-accent)_12%,transparent)]"
              >⟳ Replace Image</button>
              <button
                onClick={() => {
                  saveToHistory();
                  setSegments(prev => prev.filter(s => s.segment_id !== selectedSegmentId));
                  updateTotalDuration();
                  setSelectedSegmentId(null);
                  autoSave();
                }}
                className="text-xs px-2 py-0.5 rounded border border-[color:var(--garden-danger)] text-[color:var(--garden-danger)] hover:bg-[color-mix(in_srgb,var(--garden-danger)_12%,transparent)]"
              >✕ Delete Scene</button>
              <button
                onClick={() => {
                  addInputRef.current?.click();
                }}
                className="text-xs px-2 py-0.5 rounded border border-[color:var(--garden-warning)] text-[color:var(--garden-warning)] hover:bg-[color-mix(in_srgb,var(--garden-warning)_12%,transparent)]"
              >+ Add Scene After</button>
              <button
                onClick={() => setSelectedSegmentId(null)}
                className="text-xs px-2 py-0.5 rounded border border-[color:var(--garden-border)] text-[color:var(--garden-text-soft)] hover:border-[color:var(--garden-accent)]"
              >Deselect</button>
            </div>
          )}
          <div
            ref={timelineOuterRef}
            className="overflow-x-auto overflow-y-hidden rounded-xl relative"
            style={{ backgroundColor: 'var(--garden-timeline)' }}
          >
            <div
              style={{
                width: timelineContainerWidth * timelineZoom,
                minWidth: '100%',
              }}
            >
              <div
                className="relative"
                style={{ height: '4.8rem' }}
                onClick={(e) => {
                  if ((e.target as HTMLElement).closest('[data-timeline-segment]')) return;
                  setMovingSegmentId(null);
                  const outer = timelineOuterRef.current;
                  if (!outer) return;
                  const rect = outer.getBoundingClientRect();
                  const scrollLeft = outer.scrollLeft;
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
                  let didSaveResizeHistory = false;
                  const onMove = (e: MouseEvent) => {
                    if (!dragRef.current) return;
                    const innerEl = timelineInnerRef.current;
                    const w = innerEl ? innerEl.offsetWidth : innerWidth;
                    const delta = ((e.clientX - dragRef.current.startX) / w) * totalDuration;
                    if (!didSaveResizeHistory) {
                      saveToHistory();
                      didSaveResizeHistory = true;
                    }
                    /**
                     * Trim while dragging: snap to neighbors + playhead + timeline ends.
                     * Mouseup does not re-snap — so you can release in a gap without a jump.
                     */
                    const snapThreshold = timelineSnapThresholdSec(totalDuration);
                    let nextSegs: Segment[] | null = null;
                    setSegments((prev) => {
                      const self = prev.find((s) => s.segment_id === dragRef.current!.segmentId);
                      if (!self) return prev;
                      let newStart = Math.max(0, Math.min(self.end - 0.5, dragRef.current!.startStart + delta));
                      const snapPoints = [
                        currentTimeRef.current,
                        0,
                        totalDuration,
                        ...prev.filter((s) => s.segment_id !== self.segment_id).flatMap((s) => [s.start, s.end]),
                      ];
                      newStart = snapTimeToNearest(newStart, snapPoints, snapThreshold);
                      newStart = Math.max(0, Math.min(self.end - 0.5, newStart));
                      nextSegs = prev.map((s) => (s.segment_id === self.segment_id ? { ...s, start: newStart } : s));
                      return nextSegs;
                    });
                    if (nextSegs) updateTotalDuration(nextSegs);
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
                  let didSaveResizeHistory = false;
                  const onMove = (e: MouseEvent) => {
                    if (!dragRef.current) return;
                    const innerEl = timelineInnerRef.current;
                    const w = innerEl ? innerEl.offsetWidth : innerWidth;
                    const delta = ((e.clientX - dragRef.current.startX) / w) * totalDuration;
                    if (!didSaveResizeHistory) {
                      saveToHistory();
                      didSaveResizeHistory = true;
                    }
                    /**
                     * Trim while dragging: snap to neighbors + playhead + timeline ends.
                     * Mouseup does not re-snap — so you can release in a gap without a jump.
                     */
                    const snapThreshold = timelineSnapThresholdSec(totalDuration);
                    let nextSegs: Segment[] | null = null;
                    setSegments((prev) => {
                      const self = prev.find((s) => s.segment_id === dragRef.current!.segmentId);
                      if (!self) return prev;
                      let newEnd = Math.max(self.start + 0.5, Math.min(totalDuration, dragRef.current!.startEnd + delta));
                      const snapPoints = [
                        currentTimeRef.current,
                        0,
                        totalDuration,
                        ...prev.filter((s) => s.segment_id !== self.segment_id).flatMap((s) => [s.start, s.end]),
                      ];
                      newEnd = snapTimeToNearest(newEnd, snapPoints, snapThreshold);
                      newEnd = Math.max(self.start + 0.5, Math.min(totalDuration, newEnd));
                      nextSegs = prev.map((s) => (s.segment_id === self.segment_id ? { ...s, end: newEnd } : s));
                      return nextSegs;
                    });
                    if (nextSegs) updateTotalDuration(nextSegs);
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
                const imgPath = imagePathForFileApi(seg.image);
                const isUnmatched = seg.matched === false;
                /** Only the selected clip gets active trim handles (avoids grabbing a neighbor's edge). */
                const canTrimEdges = selectedSegmentId === seg.segment_id;
                const segBarStyle = {
                  left,
                  width,
                  height: '60%',
                  top: '20%',
                  backgroundColor: isUnmatched ? 'var(--garden-unmatched-bg)' : 'var(--garden-seg-bg)',
                  borderColor:
                    (movingSegmentId === seg.segment_id && isSnapping)
                      ? 'var(--garden-snap)'
                      : isUnmatched
                        ? (selectedSegmentId === seg.segment_id ? 'var(--garden-accent)' : 'var(--garden-unmatched-border)')
                        : (selectedSegmentId === seg.segment_id ? 'var(--garden-accent)' : 'var(--garden-seg-border)'),
                  borderStyle: isUnmatched ? 'dashed' : 'solid',
                  boxShadow: movingSegmentId === seg.segment_id
                    ? (isSnapping ? '0 0 0 3px var(--garden-gold)' : '0 0 0 2px var(--garden-gold)')
                    : selectedSegmentId === seg.segment_id ? '0 0 0 2px var(--garden-accent)' : 'none',
                  opacity: isUnmatched ? 0.75 : 1,
                  zIndex: (movingSegmentId === seg.segment_id ||
                           selectedSegmentId === seg.segment_id) ? 10 : 1,
                  cursor: movingSegmentId === seg.segment_id ? 'move' : undefined,
                };
                return (
                  <div
                    key={seg.segment_id}
                    data-timeline-segment="main"
                    className="absolute rounded border flex items-center gap-2 overflow-hidden"
                    onMouseDown={(e) => {
                      e.stopPropagation();

                      const startY = e.clientY;
                      const isFromMainTrack = true; // we'll use this

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
                          saveToHistory();
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
                          currentTimeRef.current,
                          ...liveSnapSegments
                            .filter((s) => s.segment_id !== segmentId)
                            .flatMap((s) => [s.start, s.end]),
                        ];

                        let newStart = Math.max(0, Math.min(totalDuration - duration, startStart + delta));
                        let newEnd = newStart + duration;

                        const snapThreshold = timelineSnapThresholdSec(totalDuration);
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
                        } else {
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
                      className={`absolute left-0 top-0 h-full cursor-ew-resize hover:opacity-100 ${canTrimEdges ? 'w-3' : 'w-2'}`}
                      style={{
                        backgroundColor: 'var(--garden-accent)',
                        opacity: canTrimEdges ? 0.5 : 0,
                        pointerEvents: canTrimEdges ? 'auto' : 'none',
                        zIndex: canTrimEdges ? 25 : 0,
                      }}
                      onMouseDown={canTrimEdges ? onLeftMouseDown : undefined}
                      aria-hidden
                    />
                    <div className="flex-1 flex items-center gap-2 pl-3 pr-3 min-w-0">
                      <img
                        src={fileUrl(id, imgPath)}
                        alt=""
                        className="object-cover flex-shrink-0"
                        width={40}
                        height={25}
                      />
                      <span className="text-xs text-[color:var(--garden-text)]">Scene {idx + 1}</span>
                      <span className="text-xs text-[color:var(--garden-text-soft)]">{formatTime(duration)}</span>
                      {seg.matched === false && (
                        <span className="text-xs px-1.5 py-0.5 rounded text-[color:var(--garden-text)]" style={{ backgroundColor: 'var(--garden-warning)', opacity: 0.85 }}>Unmatched</span>
                      )}
                      {seg.matched === true && seg.match_ratio > 0 && seg.match_ratio < 75 && (
                        <span
                          className="text-xs px-1.5 py-0.5 rounded"
                          style={{ backgroundColor: 'var(--garden-unmatched-border)', color: 'var(--garden-cream)' }}
                        >
                          ~{Math.round(seg.match_ratio)}%
                        </span>
                      )}
                    </div>
                    <div
                      className={`absolute right-0 top-0 h-full cursor-ew-resize hover:opacity-100 ${canTrimEdges ? 'w-3' : 'w-2'}`}
                      style={{
                        backgroundColor: 'var(--garden-accent)',
                        opacity: canTrimEdges ? 0.5 : 0,
                        pointerEvents: canTrimEdges ? 'auto' : 'none',
                        zIndex: canTrimEdges ? 25 : 0,
                      }}
                      onMouseDown={canTrimEdges ? onRightMouseDown : undefined}
                      aria-hidden
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
                    backgroundColor: 'var(--garden-playhead)',
                  }}
                />
              )}
                </div>
              </div>
            <div
              className="relative h-[60px] mt-4 border-t border-[color:var(--garden-border)] pointer-events-auto"
              style={{ display: parkedSegments.length > 0 ? 'block' : 'none' }}
              data-parking-track
            >
              <div className="absolute inset-0 rounded pointer-events-none bg-[color:color-mix(in_srgb,var(--garden-well)_70%,transparent)]" />
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
                    backgroundColor: isUnmatched ? 'var(--garden-unmatched-bg)' : 'var(--garden-seg-bg)',
                    borderColor:
                      (movingSegmentId === seg.segment_id && isSnapping)
                        ? 'var(--garden-snap)'
                        : isUnmatched
                          ? (selectedSegmentId === seg.segment_id ? 'var(--garden-accent)' : 'var(--garden-unmatched-border)')
                          : (selectedSegmentId === seg.segment_id ? 'var(--garden-accent)' : 'var(--garden-seg-border)'),
                    borderStyle: isUnmatched ? 'dashed' : 'solid',
                    boxShadow:
                      movingSegmentId === seg.segment_id
                        ? (isSnapping ? '0 0 0 3px var(--garden-gold)' : '0 0 0 2px var(--garden-gold)')
                        : selectedSegmentId === seg.segment_id ? '0 0 0 2px var(--garden-accent)' : 'none',
                    opacity: isUnmatched ? 0.75 : 1,
                    zIndex: (movingSegmentId === seg.segment_id || selectedSegmentId === seg.segment_id) ? 10 : 1,
                    cursor: movingSegmentId === seg.segment_id ? 'move' : undefined,
                  };
                  return (
                    <div
                      key={seg.segment_id}
                      data-timeline-segment="parked"
                      className="absolute rounded border flex items-center gap-2 overflow-hidden"
                      onMouseDown={(e) => {
                        e.stopPropagation();

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
                          }
                        };

                        window.addEventListener('mousemove', onMove);
                        window.addEventListener('mouseup', onUp);
                      }}
                      style={segBarStyle}
                    >
                      <div className="flex-1 flex items-center gap-2 pl-3 pr-3 min-w-0">
                        <img
                          src={fileUrl(id, imagePathForFileApi(seg.image))}
                          alt=""
                          className="object-cover flex-shrink-0"
                          width={32}
                          height={20}
                        />
                        <span className="text-xs text-[color:var(--garden-text-soft)]">PARKED Scene {idx + 1}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="absolute -top-3 left-1/2 -translate-x-1/2 text-[10px] text-[color:var(--garden-step-muted)] tracking-widest">
                PARKING TRACK (drag clips here to park • drag up to restore)
              </div>
            </div>
            </div>
          </div>
        </div>
      </main>

      {showExportOverlay && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ backgroundColor: 'var(--garden-overlay)' }}>
          <div className="rounded-2xl p-8 w-96 border border-[color:var(--garden-border)] shadow-lg" style={{ backgroundColor: 'var(--garden-card)' }}>
            <p className="text-[color:var(--garden-text)] mb-2">{exportStatus || 'Preparing…'}</p>
            {!exportError && !exportDone && (
              <div className="h-2 w-full rounded-full overflow-hidden mb-4" style={{ backgroundColor: 'var(--garden-well)' }}>
                <div className="h-full animate-pulse" style={{ width: '60%', backgroundColor: 'var(--garden-accent)' }} />
              </div>
            )}
            {exportError && <p className="text-[color:var(--garden-danger)] text-sm mb-4">{exportError}</p>}
            {exportDone && exportSubtitleQcWarnings && exportSubtitleQcWarnings.length > 0 && (
              <div
                className="text-sm mb-4 max-h-48 overflow-y-auto rounded-lg p-3 border"
                style={{
                  borderColor: 'var(--garden-border)',
                  backgroundColor: 'color-mix(in srgb, var(--garden-warning, #b45309) 12%, var(--garden-card))',
                  color: 'var(--garden-text)',
                }}
              >
                <p className="font-semibold mb-2" style={{ color: 'var(--garden-warning, #b45309)' }}>
                  Subtitle QC notes (export completed anyway)
                </p>
                <ul className="list-disc pl-5 space-y-1 text-xs" style={{ color: 'var(--garden-text-soft)' }}>
                  {exportSubtitleQcWarnings.map((line, i) => (
                    <li key={i}>{line}</li>
                  ))}
                </ul>
              </div>
            )}
            {exportDone && (
              <a
                href={fileUrl(id, 'output.mp4')}
                download
                target="_blank"
                rel="noreferrer"
                className="block text-[color:var(--garden-accent)] font-medium mb-4 hover:underline"
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
                  setExportSubtitleQcWarnings(null);
                }}
                disabled={!exportDone && !exportError}
                className="rounded px-4 py-2 text-sm font-medium text-[color:var(--garden-cream)] disabled:opacity-50 disabled:cursor-not-allowed"
                style={{ backgroundColor: 'var(--garden-accent)' }}
              >
                Close
              </button>
              {exportDone && (
                <button
                  type="button"
                  onClick={async () => { try { await stepComplete(id, 6); } catch {} router.push(`/project/${encodeProjectIdForRoute(id)}#step-7`); }}
                  className="rounded px-4 py-2 text-sm font-medium text-[color:var(--garden-cream)]"
                  style={{ backgroundColor: 'var(--garden-accent-hover)' }}
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
