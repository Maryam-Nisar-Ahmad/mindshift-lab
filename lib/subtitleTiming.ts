/** Align word timings within each cue when gaps are large (matches backend conditional snap). */

export type SubtitleWord = { word: string; start: number; end: number };

export type SubtitleLineLike = {
  start: number;
  end: number;
  text: string;
  words: SubtitleWord[];
};

/** Mirrors backend MAX_INTRA_GAP_SEC (~0.10s): skip snap when already tight vs forced alignment */
const SNAP_GAP_THRESHOLD_SEC = 0.12;

export function snapSubtitleLines<T extends SubtitleLineLike>(lines: T[]): T[] {
  return lines.map((line) => {
    const words = line.words;
    if (!words || words.length < 2) return line;
    let maxGap = 0;
    for (let i = 0; i < words.length - 1; i++) {
      const g = Math.max(0, words[i + 1].start - words[i].end);
      if (g > maxGap) maxGap = g;
    }
    if (maxGap <= SNAP_GAP_THRESHOLD_SEC) return line;

    const start = words[0].start;
    const end = words[words.length - 1].end;
    const span = Math.max(end - start, 0.001);
    const weights = words.map((w) => Math.max(w.end - w.start, 0.0001));
    const sw = weights.reduce((a, b) => a + b, 0);
    const newWords = words.map((w) => ({ ...w }));
    let t = start;
    for (let i = 0; i < newWords.length; i++) {
      const dur = span * (weights[i] / sw);
      newWords[i].start = Math.round(t * 1000) / 1000;
      t += dur;
      newWords[i].end = Math.round(t * 1000) / 1000;
    }
    newWords[newWords.length - 1].end = Math.round(end * 1000) / 1000;
    return {
      ...line,
      start: newWords[0].start,
      end: newWords[newWords.length - 1].end,
      words: newWords,
    };
  });
}
