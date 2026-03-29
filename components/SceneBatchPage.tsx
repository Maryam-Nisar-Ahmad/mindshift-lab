'use client';

import { useState, useEffect } from 'react';

type PromptItem = {
  segment_id: number;
  segment_text: string;
  mj_line: string;
};

type SceneBatchPageProps = {
  prompts: PromptItem[];
  projectId: string;
  onRegenerate: (segId: number, segText: string) => Promise<{ mj_line: string }>;
};

const PER_PAGE = 13;
const SEQ_KEY = (id: string) => `mindshift_seq_${id}`;
const COPIED_KEY = (id: string) => `mindshift_copied_${id}`;

function loadSequenceIndex(projectId: string): number {
  if (typeof window === 'undefined') return 0;
  try {
    const v = localStorage.getItem(SEQ_KEY(projectId));
    if (v == null) return 0;
    const n = parseInt(v, 10);
    return Number.isNaN(n) ? 0 : n;
  } catch {
    return 0;
  }
}

function loadCopiedIds(projectId: string): Set<number> {
  if (typeof window === 'undefined') return new Set();
  try {
    const v = localStorage.getItem(COPIED_KEY(projectId));
    if (!v) return new Set();
    const arr = JSON.parse(v) as number[];
    return Array.isArray(arr) ? new Set(arr) : new Set();
  } catch {
    return new Set();
  }
}

function saveSequenceIndex(projectId: string, idx: number) {
  try {
    localStorage.setItem(SEQ_KEY(projectId), String(idx));
  } catch {}
}

function saveCopiedIds(projectId: string, ids: Set<number>) {
  try {
    localStorage.setItem(COPIED_KEY(projectId), JSON.stringify([...ids]));
  } catch {}
}

export default function SceneBatchPage({ prompts, projectId, onRegenerate }: SceneBatchPageProps) {
  const [currentPage, setCurrentPage] = useState(1);
  const [sequenceIndex, setSequenceIndex] = useState(0);
  const [copiedIds, setCopiedIds] = useState<Set<number>>(new Set());
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const [regenLoadingId, setRegenLoadingId] = useState<number | null>(null);
  const [localPrompts, setLocalPrompts] = useState<PromptItem[]>([]);

  useEffect(() => {
    setLocalPrompts([...prompts]);
  }, [prompts]);

  useEffect(() => {
    setSequenceIndex(loadSequenceIndex(projectId));
    setCopiedIds(loadCopiedIds(projectId));
  }, [projectId]);

  const total = localPrompts.length;
  const totalPages = Math.max(1, Math.ceil(total / PER_PAGE));
  const start = (currentPage - 1) * PER_PAGE;
  const pageItems = localPrompts.slice(start, start + PER_PAGE);

  const handleCopySequence = () => {
    if (total === 0) return;
    const idx = sequenceIndex;
    if (idx >= total) return;
    const item = localPrompts[idx];
    navigator.clipboard.writeText(item.mj_line);
    setCopiedIds((prev) => {
      const next = new Set(prev);
      next.add(localPrompts[idx].segment_id);
      saveCopiedIds(projectId, next);
      return next;
    });
    const nextIdx = idx + 1;
    setSequenceIndex(nextIdx);
    saveSequenceIndex(projectId, nextIdx);
    if (nextIdx < total) {
      const nextPage = Math.floor(nextIdx / PER_PAGE) + 1;
      if (nextPage !== currentPage) setCurrentPage(nextPage);
    }
  };

  const handleToggleExpand = (segmentId: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(segmentId)) next.delete(segmentId);
      else next.add(segmentId);
      return next;
    });
  };

  const handleCopyCard = (segmentId: number, mjLine: string) => {
    navigator.clipboard.writeText(mjLine);
    setCopiedIds((prev) => {
      const next = new Set(prev);
      next.add(segmentId);
      saveCopiedIds(projectId, next);
      return next;
    });
  };

  const handleRegen = async (seg: PromptItem) => {
    setRegenLoadingId(seg.segment_id);
    try {
      const { mj_line } = await onRegenerate(seg.segment_id, seg.segment_text);
      setLocalPrompts((prev) =>
        prev.map((p) => (p.segment_id === seg.segment_id ? { ...p, mj_line } : p))
      );
      setCopiedIds((prev) => {
        const next = new Set(prev);
        next.delete(seg.segment_id);
        saveCopiedIds(projectId, next);
        return next;
      });
    } finally {
      setRegenLoadingId(null);
    }
  };

  const seqLabel =
    sequenceIndex === 0
      ? 'Copy Scene 1 Prompt'
      : sequenceIndex < total
        ? `Copied — Click for Scene ${sequenceIndex + 1} Prompt`
        : `All ${total} Prompts Copied`;
  const seqDone = total > 0 && sequenceIndex >= total;
  const seqMid = total > 0 && sequenceIndex > 0 && sequenceIndex < total;

  return (
    <div className="space-y-4">
      <div
        className="rounded-lg border p-3 mb-4 text-sm"
        style={{
          borderColor: 'var(--garden-warning)',
          backgroundColor: 'color-mix(in srgb, var(--garden-warning) 14%, var(--garden-card))',
          color: 'var(--garden-text)',
        }}
      >
        Save images as 1.png, 2.png... into:
        <br />
        MindShift-Lab/videos/{projectId}/images/
      </div>

      <button
        type="button"
        className="w-full rounded-lg py-2.5 text-sm font-medium"
        style={{
          backgroundColor: seqMid ? 'var(--garden-danger)' : 'var(--garden-accent)',
          color: 'var(--garden-cream)',
        }}
        onClick={handleCopySequence}
        disabled={total === 0 || seqDone}
      >
        {seqLabel}
      </button>

      <div className="grid grid-cols-3 gap-4">
        {pageItems.map((seg) => {
          const expanded = expandedIds.has(seg.segment_id);
          const copied = copiedIds.has(seg.segment_id);
          const loading = regenLoadingId === seg.segment_id;
          return (
            <div
              key={seg.segment_id}
              className="rounded-xl p-4 cursor-pointer border"
              style={
                copied
                  ? {
                      borderColor: 'var(--garden-lilac)',
                      backgroundColor: 'color-mix(in srgb, var(--garden-lilac) 22%, var(--garden-card))',
                    }
                  : {
                      borderColor: 'var(--garden-border)',
                      backgroundColor: 'var(--garden-card)',
                    }
              }
              onClick={() => handleToggleExpand(seg.segment_id)}
            >
              <div className="flex items-center justify-between gap-2 mb-2">
                <span
                  className="rounded text-xs font-medium px-2 py-0.5 shrink-0"
                  style={{ backgroundColor: 'var(--garden-accent)', color: 'var(--garden-cream)' }}
                >
                  Scene {seg.segment_id}
                </span>
              </div>
              <p
                className={`font-mono text-xs mb-3 ${expanded ? '' : 'line-clamp-2'}`}
                style={{ color: 'var(--garden-text-soft)' }}
              >
                {seg.mj_line}
              </p>
              <div className="flex gap-2" onClick={(e) => e.stopPropagation()}>
                <button
                  type="button"
                  className="rounded border px-2 py-1 text-xs font-medium hover:bg-[color-mix(in_srgb,var(--garden-accent)_12%,transparent)]"
                  style={{ borderColor: 'var(--garden-accent)', color: 'var(--garden-accent)' }}
                  onClick={() => handleCopyCard(seg.segment_id, seg.mj_line)}
                >
                  Copy
                </button>
                <button
                  type="button"
                  className="rounded border px-2 py-1 text-xs font-medium hover:bg-[color-mix(in_srgb,var(--garden-warning)_14%,transparent)] disabled:opacity-50"
                  style={{ borderColor: 'var(--garden-warning)', color: 'var(--garden-warning)' }}
                  onClick={() => handleRegen(seg)}
                  disabled={loading}
                >
                  {loading ? (
                    <span
                      className="inline-block h-3 w-3 border-2 rounded-full animate-spin border-t-transparent"
                      style={{ borderColor: 'var(--garden-warning)' }}
                    />
                  ) : (
                    'Regen'
                  )}
                </button>
              </div>
            </div>
          );
        })}
      </div>

      <div className="flex items-center justify-center gap-4 pt-4">
        <button
          type="button"
          className="rounded px-3 py-1.5 text-sm disabled:opacity-50 border border-[color:var(--garden-border)] hover:border-[color:var(--garden-accent)]"
          style={{ backgroundColor: 'var(--garden-card)', color: 'var(--garden-text-soft)' }}
          onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
          disabled={currentPage <= 1}
        >
          Prev
        </button>
        <span className="text-sm" style={{ color: 'var(--garden-text-soft)' }}>
          Page {currentPage} of {totalPages}
        </span>
        <button
          type="button"
          className="rounded px-3 py-1.5 text-sm disabled:opacity-50 border border-[color:var(--garden-border)] hover:border-[color:var(--garden-accent)]"
          style={{ backgroundColor: 'var(--garden-card)', color: 'var(--garden-text-soft)' }}
          onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
          disabled={currentPage >= totalPages}
        >
          Next
        </button>
      </div>
    </div>
  );
}
