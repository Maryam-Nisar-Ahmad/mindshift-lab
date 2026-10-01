'use client';

import { useState, useEffect, useMemo } from 'react';
import { insertGrokImageSlot } from '@/lib/api';

type PromptItem = {
  segment_id: number;
  segment_text: string;
  target_image?: string;
  mj_line: string;
};

type SceneBatchPageProps = {
  prompts: PromptItem[];
  projectId: string;
  onRegenerate: (segId: number, segText: string) => Promise<{ mj_line: string; target_image?: string }>;
  /** Display label for which engine prompts target */
  promptEngine?: 'midjourney' | 'grok';
};

const PER_PAGE = 13;
const PROMPT_BATCH_SIZE = 20;
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

function intentLine(text: string): string {
  const clean = (text || '').replace(/\s+/g, ' ').trim();
  if (!clean) return 'No segment summary available.';
  return clean.length > 120 ? `${clean.slice(0, 117)}...` : clean;
}

function promptMatchesSearch(p: PromptItem, rawQuery: string): boolean {
  const q = rawQuery.trim().toLowerCase();
  if (!q) return true;
  if (/^\d+$/.test(rawQuery.trim()) && p.segment_id === parseInt(rawQuery.trim(), 10)) {
    return true;
  }
  const blob = [
    String(p.segment_id),
    (p.segment_text || '').toLowerCase(),
    (p.mj_line || '').toLowerCase(),
    (p.target_image || '').toLowerCase(),
  ].join('\n');
  return blob.includes(q);
}

/** Full prompts in document order, separated by a blank line (Grok batch paste/export). */
function buildAllPromptsPlaintext(items: PromptItem[]): string {
  return items
    .map((p) => (p.mj_line || '').trim())
    .join('\n\n');
}

export default function SceneBatchPage({ prompts, projectId, onRegenerate, promptEngine = 'midjourney' }: SceneBatchPageProps) {
  const [currentPage, setCurrentPage] = useState(1);
  const [sequenceIndex, setSequenceIndex] = useState(0);
  const [copiedIds, setCopiedIds] = useState<Set<number>>(new Set());
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const [regenLoadingId, setRegenLoadingId] = useState<number | null>(null);
  const [localPrompts, setLocalPrompts] = useState<PromptItem[]>([]);
  const [allPromptsCopied, setAllPromptsCopied] = useState(false);
  const [nextBatchStart, setNextBatchStart] = useState(0);
  const [selectedBatchIndex, setSelectedBatchIndex] = useState(0);
  const [slotInserting, setSlotInserting] = useState<number | null>(null);
  const [slotFeedback, setSlotFeedback] = useState<string | null>(null);
  const [promptSearch, setPromptSearch] = useState('');

  useEffect(() => {
    setLocalPrompts([...prompts]);
  }, [prompts]);

  useEffect(() => {
    setSequenceIndex(loadSequenceIndex(projectId));
    setCopiedIds(loadCopiedIds(projectId));
  }, [projectId]);

  const total = localPrompts.length;
  const sortedPrompts = [...localPrompts].sort((a, b) => a.segment_id - b.segment_id);
  const filteredForView = useMemo(() => {
    if (!promptSearch.trim()) return localPrompts;
    return localPrompts.filter((p) => promptMatchesSearch(p, promptSearch)).sort((a, b) => a.segment_id - b.segment_id);
  }, [localPrompts, promptSearch]);
  const searchActive = Boolean(promptSearch.trim());
  const viewTotal = filteredForView.length;
  const totalBatches = Math.max(1, Math.ceil(total / PROMPT_BATCH_SIZE));
  const totalPages = Math.max(1, Math.ceil(viewTotal / PER_PAGE));
  const start = (currentPage - 1) * PER_PAGE;
  const pageItems = filteredForView.slice(start, start + PER_PAGE);

  useEffect(() => {
    setNextBatchStart((prev) => (prev < total ? prev : 0));
    setSelectedBatchIndex((prev) => (prev < totalBatches ? prev : 0));
  }, [total, totalBatches]);

  useEffect(() => {
    const tp = Math.max(1, Math.ceil(viewTotal / PER_PAGE));
    setCurrentPage((p) => (p > tp ? tp : p));
  }, [viewTotal]);

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
    // Pagination follows filtered list when searching; sequence index follows full list — skip auto page jump.
    if (!promptSearch.trim() && nextIdx < total) {
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
      const { mj_line, target_image } = await onRegenerate(seg.segment_id, seg.segment_text);
      setLocalPrompts((prev) =>
        prev.map((p) =>
          p.segment_id === seg.segment_id ? { ...p, mj_line, target_image: target_image ?? p.target_image } : p
        )
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

  const unit = promptEngine === 'grok' ? 'Prompt' : 'Scene';
  const unitLower = promptEngine === 'grok' ? 'prompt' : 'scene';
  const fixGridPrompts = searchActive ? filteredForView : sortedPrompts;
  const seqLabel =
    sequenceIndex === 0
      ? `Copy ${unit} 1`
      : sequenceIndex < total
        ? `Copied — Click for ${unit} ${sequenceIndex + 1}`
        : `All ${total} copied`;
  const seqDone = total > 0 && sequenceIndex >= total;
  const seqMid = total > 0 && sequenceIndex > 0 && sequenceIndex < total;

  const allPromptsText = buildAllPromptsPlaintext(sortedPrompts);
  const selectedBatchStart = selectedBatchIndex * PROMPT_BATCH_SIZE;
  const selectedBatchItems = sortedPrompts.slice(selectedBatchStart, selectedBatchStart + PROMPT_BATCH_SIZE);
  const selectedBatchText = buildAllPromptsPlaintext(selectedBatchItems);
  const selectedBatchEnd = selectedBatchStart + selectedBatchItems.length;
  const nextBatchItems = sortedPrompts.slice(nextBatchStart, nextBatchStart + PROMPT_BATCH_SIZE);
  const nextBatchText = buildAllPromptsPlaintext(nextBatchItems);
  const nextBatchEnd = nextBatchStart + nextBatchItems.length;
  const nextBatchDone = nextBatchStart >= total || total === 0;

  const handleCopyAllPrompts = () => {
    if (!allPromptsText) return;
    navigator.clipboard.writeText(allPromptsText).then(() => {
      setAllPromptsCopied(true);
      window.setTimeout(() => setAllPromptsCopied(false), 2000);
    });
  };

  const handleCopyNextBatch = () => {
    if (!nextBatchText) return;
    navigator.clipboard.writeText(nextBatchText).then(() => {
      const next = nextBatchStart + PROMPT_BATCH_SIZE;
      setNextBatchStart(next);
      if (next < totalBatches * PROMPT_BATCH_SIZE) {
        setSelectedBatchIndex(Math.floor(next / PROMPT_BATCH_SIZE));
      }
    });
  };

  const handleDownloadAllPrompts = () => {
    if (!allPromptsText) return;
    const blob = new Blob([allPromptsText], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `grok-scene-prompts-${projectId}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleDownloadSelectedBatch = () => {
    if (!selectedBatchText) return;
    const blob = new Blob([selectedBatchText], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `grok-scene-prompts-${projectId}-${selectedBatchStart + 1}-${selectedBatchEnd}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleInsertGrokImageSlot = async (segmentId: number) => {
    const label = `download (${segmentId}).jfif`;
    if (
      !window.confirm(
        `Make an empty file slot for Prompt ${segmentId}?\n\nGrok-style files from this position onward in images/ will be renamed +1 (e.g. the old file for this slot → next name) so the missing file can be saved as ${label}.\n\nNumbered stills (1.png, 2.jpg, …) are not moved.`
      )
    ) {
      return;
    }
    setSlotInserting(segmentId);
    setSlotFeedback(null);
    try {
      const r = await insertGrokImageSlot(projectId, segmentId);
      const n = r.renamed?.length ?? 0;
      if (n === 0) {
        setSlotFeedback('Nothing to shift: no Grok download–named files on or after that position (or the slot is already after the last file).');
        return;
      }
      setSlotFeedback(
        `Renamed ${n} file(s). Save the missing still as ${r.save_next_as} (e.g. ${label}).`
      );
    } catch (e) {
      setSlotFeedback(e instanceof Error ? e.message : 'Request failed');
    } finally {
      setSlotInserting(null);
    }
  };

  const mainColumn = (
    <>
      <div
        className="rounded-lg border p-3 mb-4 text-sm"
        style={{
          borderColor: 'var(--garden-warning)',
          backgroundColor: 'color-mix(in srgb, var(--garden-warning) 14%, var(--garden-card))',
          color: 'var(--garden-text)',
        }}
      >
        <span className="font-semibold" style={{ color: 'var(--garden-text)' }}>
          {promptEngine === 'grok' ? 'Grok' : 'Midjourney'}
        </span>
        : copy each line into your image tool. Save files in sequence as download (1).jfif, download (2).jfif, download (3).jfif, … (scene number = number in parentheses) into:
        <br />
        backend/videos/{projectId}/images/
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

      {searchActive && viewTotal === 0 ? (
        <p
          className="rounded-lg border px-4 py-6 text-center text-sm"
          style={{ borderColor: 'var(--garden-border)', color: 'var(--garden-text-soft)' }}
        >
          No prompts match your search. Try a {unitLower} number, a phrase from the script line, or words from the image prompt.
        </p>
      ) : (
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
                        borderColor: 'var(--fun-bubble)',
                        backgroundColor: 'color-mix(in srgb, var(--fun-bubble) 38%, var(--garden-card))',
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
                    {promptEngine === 'grok' ? 'Prompt' : 'Scene'} {seg.segment_id}
                  </span>
                </div>
                <p
                  className="text-[11px] mb-2 rounded-md px-2 py-1"
                  style={{
                    color: 'var(--garden-text)',
                    backgroundColor: 'color-mix(in srgb, var(--garden-accent) 10%, transparent)',
                  }}
                  title={seg.segment_text}
                >
                  Target image: {intentLine(seg.target_image || seg.segment_text)}
                </p>
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
      )}

      <div className="flex items-center justify-center gap-4 pt-4">
        <button
          type="button"
          className="rounded px-3 py-1.5 text-sm disabled:opacity-50 border border-[color:var(--garden-border)] hover:border-[color:var(--garden-accent)]"
          style={{ backgroundColor: 'var(--garden-card)', color: 'var(--garden-text-soft)' }}
          onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
          disabled={currentPage <= 1 || viewTotal === 0}
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
          disabled={currentPage >= totalPages || viewTotal === 0}
        >
          Next
        </button>
      </div>
    </>
  );

  return (
    <div className="space-y-4">
      {total > 0 ? (
        <div
          className="rounded-lg border p-3"
          style={{ borderColor: 'var(--garden-border)', backgroundColor: 'var(--garden-card)' }}
        >
          <label className="block text-xs font-semibold mb-1.5" htmlFor={`prompt-search-${projectId}`} style={{ color: 'var(--garden-text)' }}>
            Search prompts
          </label>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
            <input
              id={`prompt-search-${projectId}`}
              type="search"
              autoComplete="off"
              className="min-w-0 flex-1 rounded-md border px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[color:var(--garden-accent)]"
              style={{
                borderColor: 'var(--garden-border)',
                backgroundColor: 'var(--garden-cream, var(--garden-card))',
                color: 'var(--garden-text)',
              }}
              placeholder={`${unit} #, words from the script line, or anything in the image prompt…`}
              value={promptSearch}
              onChange={(e) => {
                setPromptSearch(e.target.value);
                setCurrentPage(1);
              }}
            />
            {promptSearch.trim() ? (
              <button
                type="button"
                className="shrink-0 rounded-md border px-3 py-2 text-sm font-medium"
                style={{ borderColor: 'var(--garden-border)', color: 'var(--garden-text-soft)' }}
                onClick={() => {
                  setPromptSearch('');
                  setCurrentPage(1);
                }}
              >
                Clear
              </button>
            ) : null}
            <span className="text-xs shrink-0 sm:ml-auto" style={{ color: 'var(--garden-text-soft)' }}>
              {searchActive
                ? viewTotal === 0
                  ? `No matches · ${total} total`
                  : `Showing ${viewTotal} of ${total}`
                : `${total} ${unitLower}${total === 1 ? '' : 's'}`}
            </span>
          </div>
        </div>
      ) : null}

      {promptEngine === 'grok' && total > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="rounded-lg border px-3 py-2 text-sm font-medium hover:bg-[color-mix(in_srgb,var(--garden-accent)_12%,transparent)] disabled:opacity-50"
            style={{ borderColor: 'var(--garden-accent)', color: 'var(--garden-accent)' }}
            onClick={handleCopyNextBatch}
            disabled={nextBatchDone}
          >
            {nextBatchStart === 0
              ? `Copy first ${Math.min(PROMPT_BATCH_SIZE, total)} prompts`
              : nextBatchStart < total
                ? `Copy next ${Math.min(PROMPT_BATCH_SIZE, total - nextBatchStart)} prompts`
                : 'All prompt batches copied'}
          </button>
          <button
            type="button"
            className="rounded-lg border px-3 py-2 text-sm font-medium hover:bg-[color-mix(in_srgb,var(--garden-accent)_12%,transparent)]"
            style={{ borderColor: 'var(--garden-accent)', color: 'var(--garden-accent)' }}
            onClick={handleCopyAllPrompts}
            disabled={!allPromptsText}
          >
            {allPromptsCopied ? 'Copied all prompts' : 'Copy all prompts'}
          </button>
          <button
            type="button"
            className="rounded-lg border px-3 py-2 text-sm font-medium hover:bg-[color-mix(in_srgb,var(--garden-accent)_12%,transparent)]"
            style={{ borderColor: 'var(--garden-border)', color: 'var(--garden-text-soft)' }}
            onClick={handleDownloadAllPrompts}
            disabled={!allPromptsText}
          >
            Export all .txt
          </button>
          <select
            className="rounded-lg border px-3 py-2 text-sm"
            style={{ borderColor: 'var(--garden-border)', backgroundColor: 'var(--garden-card)', color: 'var(--garden-text)' }}
            value={selectedBatchIndex}
            onChange={(e) => setSelectedBatchIndex(Number(e.target.value))}
          >
            {Array.from({ length: totalBatches }).map((_, idx) => {
              const startIdx = idx * PROMPT_BATCH_SIZE + 1;
              const endIdx = Math.min((idx + 1) * PROMPT_BATCH_SIZE, total);
              return (
                <option key={idx} value={idx}>
                  Batch {idx + 1}: prompts {startIdx}-{endIdx}
                </option>
              );
            })}
          </select>
          <button
            type="button"
            className="rounded-lg border px-3 py-2 text-sm font-medium hover:bg-[color-mix(in_srgb,var(--garden-accent)_12%,transparent)] disabled:opacity-50"
            style={{ borderColor: 'var(--garden-border)', color: 'var(--garden-text-soft)' }}
            onClick={() => navigator.clipboard.writeText(selectedBatchText)}
            disabled={!selectedBatchText}
          >
            Copy selected batch ({selectedBatchItems.length})
          </button>
          <button
            type="button"
            className="rounded-lg border px-3 py-2 text-sm font-medium hover:bg-[color-mix(in_srgb,var(--garden-accent)_12%,transparent)] disabled:opacity-50"
            style={{ borderColor: 'var(--garden-border)', color: 'var(--garden-text-soft)' }}
            onClick={handleDownloadSelectedBatch}
            disabled={!selectedBatchText}
          >
            Export selected batch .txt
          </button>
          <span className="text-xs" style={{ color: 'var(--garden-text-soft)' }}>
            One full prompt per block, blank line between (scene order). Next batch now covers prompts {nextBatchStart + 1}-{Math.min(nextBatchEnd, total)}.
          </span>
        </div>
      ) : null}

      {promptEngine === 'grok' && total > 0 ? (
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
          <div className="min-w-0 flex-1 space-y-4">{mainColumn}</div>
          <aside
            className="w-full shrink-0 rounded-lg border p-3 lg:sticky lg:top-4 lg:max-w-[16rem]"
            style={{ borderColor: 'var(--garden-border)', backgroundColor: 'var(--garden-card)' }}
          >
            <p className="text-xs font-semibold mb-1" style={{ color: 'var(--garden-text)' }}>
              Fix skipped download
            </p>
            <p className="text-[11px] leading-snug mb-2" style={{ color: 'var(--garden-text-soft)' }}>
              Click a prompt # if the extension skipped that still. Grok-named files in images/ at that slot and
              after shift by one filename so you can add the missing file with the right name. Numbered 1.png… files
              are not touched.
            </p>
            <div className="mb-2 grid max-h-64 grid-cols-5 gap-1.5 overflow-y-auto">
              {fixGridPrompts.map((p) => (
                <button
                  key={p.segment_id}
                  type="button"
                  disabled={slotInserting !== null}
                  onClick={() => void handleInsertGrokImageSlot(p.segment_id)}
                  className="rounded border px-1.5 py-1 text-center text-xs font-medium disabled:opacity-50"
                  style={{
                    borderColor: 'var(--garden-accent)',
                    color: 'var(--garden-accent)',
                    backgroundColor:
                      slotInserting === p.segment_id
                        ? 'color-mix(in srgb, var(--garden-accent) 20%, var(--garden-card))'
                        : 'var(--garden-card)',
                  }}
                >
                  {p.segment_id}
                </button>
              ))}
            </div>
            {slotFeedback ? (
              <p
                className="text-[11px] break-words rounded-md px-2 py-1"
                style={{
                  color: 'var(--garden-text)',
                  backgroundColor: 'color-mix(in srgb, var(--garden-accent) 8%, transparent)',
                }}
              >
                {slotFeedback}
              </p>
            ) : null}
          </aside>
        </div>
      ) : (
        <div className="space-y-4">{mainColumn}</div>
      )}
    </div>
  );
}
