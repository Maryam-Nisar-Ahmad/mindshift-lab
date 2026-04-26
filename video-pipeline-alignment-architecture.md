# Video Pipeline Architecture: Tight Image to Speech Alignment and Readable Subtitles

*A professional reference for Next.js + Python short-form/educational video pipelines combining Whisper transcription, LLM correction, fuzzy segment mapping, karaoke subtitles, canvas preview, and ffmpeg export.*

---

## Executive Summary

Professional broadcast and streaming pipelines converge on a three-stage alignment architecture: (1) **phoneme-level forced alignment** as the ground-truth time anchor, (2) **sequence-to-sequence NLP post-processing** for punctuation and segmentation, and (3) **a single authoritative clock source** that all downstream consumers follow without re-smoothing. The fuzzy-first-word matching in your current design is a reasonable bootstrap heuristic, but it accumulates error on repeated phrases and long narrations. The most impactful upgrade for tight image-to-speech alignment is replacing or supplementing it with CTC forced alignment using a pre-aligned phoneme model (wav2vec2-based), exactly what WhisperX's third stage does. The second most impactful fix is eliminating double-smoothing between your backend `_snap_subtitle_line_timings` and the frontend `requestAnimationFrame` gap-closing logic.

---

## 1. Forced Alignment vs. Fuzzy Token Matching

### The Architecture Decision

| Method | Accuracy (word boundary error) | Handles script-to-audio divergence | Compute cost | Best for |
|--------|-------------------------------|--------------------------------|-------------|---------|
| GMM-HMM (MFA) | ~10 ms median on TIMIT | Requires exact transcript | High (needs Kaldi) | Phonetics research, gold-standard QA |
| CTC wav2vec2 forced alignment (WhisperX stage 3) | ~20 to 30 ms median | Tolerates small substitutions via `<star>` token | Low to medium | Production pipelines |
| DTW on Whisper cross-attention (CrisperWhisper) | ~25 to 40 ms, better on pauses | Moderate (depends on training data) | Low (no extra model) | Single-model pipelines |
| Needleman-Wunsch global alignment on STT vs. script | N/A (timing from STT only) | Excellent for large structural drift | Very low | Subtitle re-sync from existing SRT |
| Fuzzy first-N-word matching (your current approach) | 100 to 500+ ms on repeated phrases | Poor (monotone forward search breaks on repetition) | Negligible | Bootstrapping; insufficient alone |

The landmark [2024 arXiv comparison](https://arxiv.org/abs/2406.19363) (Rousso et al., ICASSP 2024) measured word boundary error on TIMIT and Buckeye: **MFA (GMM-HMM) outperforms WhisperX and MMS at all time-resolution thresholds**, with MFA achieving F₁ > 0.95 within 20 ms. However, MFA requires a perfect transcript. For your use case, where the LLM has already corrected the transcript to match the audio word-for-word, this is achievable and worth measuring.

The [NVIDIA NeMo forced aligner blog](https://research.nvidia.com/labs/conv-ai/blogs/2023/2023-08-forced-alignment/) explains the Viterbi-on-CTC approach directly: the aligner runs the audio through a CTC model to get a probability matrix \( V \times T \), then finds the maximum-probability path through the reference token sequence via Viterbi decoding. This is the same mechanism used by `ctc-forced-aligner` ([MahmoudAshraf97/ctc-forced-aligner](https://github.com/MahmoudAshraf97/ctc-forced-aligner)) and WhisperX's alignment stage.

### CTC "Peaky" Behavior and Its Fix

CTC posteriors are notoriously peaky (most probability mass on blank or onset tokens), which degrades offset timestamps. The [Huang et al. 2024 ICASSP paper](https://ieeexplore.ieee.org/document/10446111/) on "Less Peaky CTC" shows that adding **label priors** during training reduces phoneme/word boundary error (PBE/WBE) by 12% to 40% on Buckeye/TIMIT. The pretrained model ships with TorchAudio. For your pipeline, substituting this model for the default wav2vec2 CTC in WhisperX's alignment stage is a drop-in improvement with no architectural change.

---

## 2. WhisperX in Depth: VAD + Whisper + Phoneme FA

The [WhisperX paper (Bain et al., arXiv 2303.00747)](https://arxiv.org/abs/2303.00747) describes a three-stage pipeline:

1. **Silero VAD** segments audio into speech/non-speech intervals. A "Cut & Merge" strategy joins short segments into ~30-second chunks at non-speech boundaries, enabling batched Whisper inference with a **12× speedup**.
2. **Whisper** transcribes each chunk. With `condition_on_previous_text=False`, hallucination chaining is broken (a single "thank you" cannot cascade into 28 repetitions, per [Reddit production experience](https://www.reddit.com/r/LocalLLaMA/comments/1rlqfd7/)).
3. **Phoneme CTC forced alignment** (wav2vec2 or equivalent) re-aligns each Whisper word to the audio, providing word-level timestamps accurate to ≈20 to 30 ms.

The `refine_word_timestamps` step in WhisperX specifically **closes inter-word gaps** by extending each word's end time to the start of the next word within a segment, eliminating the artificial silence that Whisper leaves between token predictions. **This is the backend snap step that must not be duplicated in the frontend.**

### CrisperWhisper: DTW Without a Second Model

[CrisperWhisper (Wagner et al., arXiv 2408.16589)](https://arxiv.org/abs/2408.16589) eliminates the wav2vec2 dependency by training Whisper with an **AttentionLoss** on specific cross-attention heads and retokenizing spaces as explicit tokens. DTW on the resulting cost matrix achieves state-of-the-art word segmentation on AMI and TED-LIUM, outperforming WhisperX in noisy conditions because Wav2Vec2's noise sensitivity does not apply. Key architectural insight: **punctuation tokens must be removed before constructing the DTW cost matrix** since they have no acoustic representation and corrupt the alignment path. CrisperWhisper applies a 160 ms pause-duration cap, splitting any longer artifact gap evenly between adjacent words.

A newer 2025 arXiv result ([Whisper Has an Internal Word Aligner](https://arxiv.org/html/2509.09987v1)) identifies specific attention heads that capture accurate word alignments, outperforming WhisperX on TIMIT/LibriSpeech and matching it on AMI, without any external aligner. These attention-selected DTW timestamps may become the default for single-model pipelines.

---

## 3. Handling Repeated Phrases and Script-to-Audio Divergence

### The Hallucination/Repetition Problem

Whisper's encoder-decoder architecture produces hallucinations and repetition loops under three conditions: silence, low-SNR audio, and prompt conditioning on previous hallucinated text. Production mitigations, ranked by impact:

1. **Silero VAD gate**: skip Whisper entirely on non-speech frames. Silero [significantly outperforms WebRTC VAD](https://arxiv.org/html/2601.17270v1) (ROC AUC) for all window sizes.
2. **`condition_on_previous_text=False`**: breaks hallucination chaining.
3. **Entropy threshold tuning**: `--entropy-thold 2.6` ([whisper.cpp discussion #2286](https://github.com/ggml-org/whisper.cpp/discussions/2286)) causes fallback when the decoder is uncertain.
4. **`beam_size=1`**: greedy decoding fails fast on silence instead of searching for plausible completions.
5. **Repeated-output detection**: force-advance timestamp if the same text appears N consecutive times.
6. **Demucs vocal separation**: extract vocals before transcription. The [2025 Jam-ALT benchmark](https://arxiv.org/html/2506.15514v1) shows that RMS-VAD on separated vocals delivers **lower WER than Whisper's native long-form algorithm** by providing better segment boundaries, not improved vocal quality. The boundary improvement is the key benefit.

### Script-to-Audio Divergence: DTW and NW Alignment

When your LLM correction step diverges from the audio (speaker ad-libs, shortened phrases, reordering), your forward-only fuzzy matching accumulates irrecoverable error. Professional solutions use **sequence alignment between the ASR hypothesis and the reference script**:

**Needleman-Wunsch (global):** Maximizes matched word pairs across the full sequence, assigning gap penalties for insertions/deletions. Best for subtitle re-synchronization where the reference script is nearly complete. The [pgdm.ch subtitle synchronization pipeline](https://c.pgdm.ch/notes/subtitles-synchronization-1/) (2024) uses NW on STT output vs. script words, fits a piecewise-linear time transformation to matched pairs, and re-synchronizes all timecodes. Gaps are penalized more leniently than mismatches, ensuring matched pairs are not corrupted by noise.

**Smith-Waterman (local):** Finds the best-scoring local alignment substring. Preferred when sections of the script are entirely absent from the audio (deleted scenes, heavy editing). Netflix's [scene boundary detection system](https://netflixtechblog.com/detecting-scene-changes-in-audiovisual-content-77a61d3eaad6) uses **DTW with sentence embeddings** (paraphrase-optimized models) between screenplay text and timestamped closed captions, tolerating local violations of the monotonicity assumption. This is the industry model for script-to-audio alignment at scale.

**CTC-Segmentation** (Kürzinger et al., [arXiv 2007.09127](https://arxiv.org/abs/2007.09127)): Uses CTC log-posteriors to segment a long audio file against a reference transcript. Unlike FA, it does not require a perfect transcript. It finds the most likely segmentation by dynamic programming over CTC scores. Best for batch-aligning long narrations with imperfect scripts.

**Practical recommendation for your pipeline:**
- For short (<5 min) educational segments with clean narration: WhisperX phoneme FA after LLM correction is sufficient.
- For longer segments or significant ad-libbing: Run CTC-Segmentation or NW alignment between the raw ASR output and your script *before* the LLM correction step, to establish coarse anchor points. Then run WhisperX FA within each anchored segment.
- For the fuzzy 3 to 6 word matching specifically: Replace `partial_ratio` with `token_set_ratio` from [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) when word order may vary, but add a **confidence gate**: only accept matches above score 85, otherwise fall through to the uniform timeline fallback rather than accepting a low-confidence anchor that corrupts all subsequent matches.

---

## 4. Scene Cut Alignment: Silence vs. Aligned Graphemes

### Industry Practice

Professional workflows at Netflix use **two complementary signals** for scene boundary placement ([Netflix TechBlog, 2023](https://netflixtechblog.com/detecting-scene-changes-in-audiovisual-content-77a61d3eaad6)):

1. **Screenplay-derived timestamps** via DTW between script and captions
2. **Shot-level embeddings** from a pretrained video+audio model, with **source separation** (wav2vec2 on separated foreground speech + background music separately) to improve audio embedding quality

For your pipeline (image stills, not continuous video), the equivalent is: **prefer aligning segment cuts to silence boundaries (VAD-detected non-speech regions) rather than to exact phoneme start times**. This is because:

- Placing a cut mid-phoneme creates an audible click artifact
- The human perceptual system is less sensitive to video-audio desync during silence (AV window ±80 ms during speech, wider during silence)
- Silence boundaries are more robustly detected than phoneme boundaries

**Practical rule:** Use `refine_word_timestamps` closes to the nearest silence gap detected by Silero VAD (30 to 100 ms min silence duration), not to the raw phoneme onset. Scene cuts (xfade start) should land within the silence window preceding the first word of the new segment.

The [2025 Jam-ALT study](https://arxiv.org/html/2506.15514v1) confirms that **RMS-VAD segment boundaries outperform Whisper's native boundaries** even on separated vocals, reinforcing silence-first boundary strategy.

---

## 5. Punctuation and Segmentation Using Standalone NLP

### Why Separate NLP (Not LLM Inline)

Your pipeline uses the LLM to correct word identity (same word count, no punctuation). This is correct. Punctuation restoration should be a **separate lightweight pass** after alignment, using a BERT-class sequence labeling model, not the correction LLM. Reasons:

1. LLMs used for punctuation during correction tend to alter "grammatically plausible but acoustically incorrect" words ([arXiv 2603.19256](https://arxiv.org/html/2603.19256v1), Bengali ASR pipeline paper)
2. A dedicated punctuation model operates at token level with <10ms latency
3. Models can be trained on ASR-output distributions, matching the noise profile of Whisper output

### Best Available Models

| Model | F1 on ASR text | Notes |
|-------|---------------|-------|
| `felflare/bert-restore-punctuation` (HuggingFace) | ~90 F1 (IWSLT) | English; drop-in BERT sequence labeler |
| `kredor/punctuate-all` | Multilingual | Based on XLM-RoBERTa |
| AppTek streaming punctuation model | 0.90 F1 (subtitle output) | Proprietary; [referenced at AppTek](https://www.apptek.ai/post/asr-in-captions-accessibility-series-article-7) |

AppTek's 2019 offline model achieves F1 = 0.92 on subtitle output. For production use, a fine-tuned `deberta-v3-base` on the IWSLT TED dataset reliably outperforms BERT at the same inference cost.

### Segmentation: Linguistically Motivated Line Breaks

The [PMC/NIH eye-tracking study (2018)](https://pmc.ncbi.nlm.nih.gov/articles/PMC7901653/) confirms that **syntactically misaligned line breaks** (e.g., article separated from noun) increase fixation duration and cognitive load measurably. AppTek's 2019 "Intelligent Line Segmentation" system uses neural sequence labeling to predict `<eol>` and `<eob>` boundary tokens, respecting hard character-per-line and duration constraints. For your karaoke-style 2 to 3 word lines, the key rule is: **never split a prepositional phrase or noun phrase across lines**. A practical approximation: run spaCy dependency parsing on the punctuated text, then place line breaks only at detected clause boundaries or after punctuation marks.

---

## 6. Avoiding Double-Smoothing of Word Timings

### The Problem

Double-smoothing occurs when:
1. Backend `refine_word_timestamps` extends each word's end time to the next word's start (gap closure)
2. Frontend `_snap_subtitle_line_timings` (or similar) applies a second proportional interpolation within each line
3. The result is that words at line boundaries are shifted in both directions simultaneously, creating **timing jitter that is uncorrelated with the actual phoneme positions**

### The Fix: Single Authoritative Timeline

**Architecture rule:** The backend (Python) is the sole authority on word timestamps. The frontend reads them as immutable. The only frontend adjustment permitted is **linear interpolation within a line when no word timestamps are available for a word** (e.g., a word not found by FA), and this must be flagged in the metadata, not silently applied.

Specifically:
- `refine_word_timestamps` closing gaps: run **once** in the Python pipeline, store result.
- `_snap_subtitle_line_timings` proportional distribution: apply **only to words that the FA marked as `score < threshold` or `timestamp = None`**. Do not re-run on words that already have FA timestamps.
- Frontend `requestAnimationFrame` delta accumulation: use for **display scheduling only**. Calculate which subtitle should be shown given elapsed time, but do not feed back into the stored timestamps.

A common mistake is implementing `_snap_subtitle_line_timings` as a re-normalization step (ensuring line start/end sum to segment duration) and running it after FA. This is correct if and only if the FA word timestamps are themselves used as interior anchors and the snap only adjusts the unmeasured inter-word silences. If it adjusts FA-measured timestamps, it degrades accuracy.

---

## 7. HTML5 Audio Clock Drift vs. Web Audio

### Why `audio.currentTime` Drifts

The [web.dev "Tale of Two Clocks" article](https://web.dev/articles/audio-scheduling) and the [WebAudio API issue #340 discussion](https://github.com/WebAudio/web-audio-api/issues/340) establish the fundamental problem: `HTMLMediaElement.currentTime` is driven by the **browser's system clock** (performance.now), which can be clamped to 1 ms resolution due to Spectre/Meltdown mitigations. More importantly, for MP3-encoded files, `currentTime` has [a well-documented inaccuracy](https://stackoverflow.com/questions/25468063/html5-audio-currenttime-attribute-inaccurate) where forward-seeking desynchronizes the reported position from the actual decode position by up to several seconds on long files.

### Your `requestAnimationFrame` Delta Approach

Your design of **accumulating `requestAnimationFrame` deltas** rather than reading `audio.currentTime` is architecturally correct for avoiding drift on long files. The subtlety is that `requestAnimationFrame` callbacks receive a `DOMHighResTimeStamp` that is synchronized with `document.timeline.currentTime` and is **frame-boundary aligned** (16.67 ms at 60 Hz). From [MDN's rAF documentation](https://developer.mozilla.org/en-US/docs/Web/API/Window/requestAnimationFrame): when synchronizing to `BaseAudioContext.currentTime`, the highest available precision is one frame duration.

**Recommended hybrid approach:**
1. On play, record `AudioContext.currentTime` as `audioStart` and `performance.now()` as `wallStart`.
2. In each rAF callback, compute `elapsed = (rAFTimestamp - wallStart) / 1000`.
3. **Drift correction**: every ~5 seconds, read `AudioContext.getOutputTimestamp().contextTime` and compare to `elapsed`. If drift > 20 ms, apply a one-shot correction offset. Do not continuously lerp. Single corrections are imperceptible; continuous lerp creates visible subtitle skip.
4. For Bluetooth/high-latency output: read `AudioContext.outputLatency` (supported in Chrome/Firefox) and subtract it from the timeline. The [Jamieonkeys.dev post on outputLatency](https://www.jamieonkeys.dev/posts/web-audio-api-output-latency/) shows this eliminates AV desync for Bluetooth headsets.

The [blog.paul.cx deep-dive on AV sync](https://blog.paul.cx/post/audio-video-synchronization-with-the-web-audio-api/) demonstrates that `AudioContext.getOutputTimestamp()` returns both `contextTime` (in audio clock domain) and `performanceTime` (in performance.now domain), enabling a clock-domain bridge. Use `performanceTime` as the reference for rAF delta accumulation.

**Why not use `<audio>` + `timeupdate`?** `timeupdate` fires at 4 to 250 Hz (browser-dependent), introducing up to 250 ms latency between state changes. rAF at 60 Hz is strictly superior for visual subtitle synchronization.

---

## 8. ffmpeg xfade: Timing Verification and the Offset Formula

### The Cumulative Offset Formula

The single most common mistake with multi-clip xfade chains is using individual clip durations instead of cumulative durations for offset calculation. The correct formula for N clips with uniform fade duration `fd`:

For clip i (0-indexed), the xfade `offset` (time from the start of the output stream where the fade begins) is:

\[
\text{offset}_i = \sum_{j=0}^{i} \text{dur}(j) - (i+1) \cdot fd
\]

From the [Stack Overflow canonical answer](https://stackoverflow.com/questions/63553906/merging-multiple-video-files-with-ffmpeg-and-xfade-filter):

| Input | Duration | Prev offset | Fade dur | **Offset** |
|-------|----------|-------------|----------|------------|
| v0 | 4 s | 0 | 1 s | **3** |
| v1 | 8 s | 3 | 1 s | **10** |
| v2 | 12 s | 10 | 1 s | **21** |
| v3 | 5 s | 21 | 1 s | **25** |

**With varying fade durations per clip:** offset at fade i = `sum(dur[0..i]) - sum(fd[0..i])`. Failing to account for accumulated transition time is the source of most "audio ending before video" bugs.

### Timing Verification with BITC

From the [r/ffmpeg thread on xfade validation](https://www.reddit.com/r/ffmpeg/comments/1cxlyu6/):
> "Use `drawtext` to add a burned-in timecode (BITC) to validate the timings of your transitions. You can validate this with a frame-step-through tool like QCTools."

Recommended QA command:
```bash
ffmpeg -i output.mp4 -vf "drawtext=text='%{pts\\:hms}':fontsize=48:x=10:y=10:fontcolor=white:box=1:boxcolor=black@0.5" -c:a copy output_bitc.mp4
```
Step through `output_bitc.mp4` at each transition to confirm the timestamp matches expected segment start times. The [ffmpeg muxing delay bug (Doom9 forum)](https://forum.doom9.org/showthread.php?t=181624) shows ffmpeg muxing ASS into MKV introduces a systematic ~50 ms subtitle delay vs. mkvtoolnix; use `-itsoffset 0.05` on the subtitle input as a workaround.

### ASS File Requirements

Both inputs to xfade must have **identical frame rate, resolution, pixel format, and timebase**. If your stills pipeline generates frames at different sizes, add `scale=W:H,setsar=1` before xfade in the filter_complex. For still images as input, use `-loop 1 -t <duration> -framerate 24 -i image.jpg` (not `-t` on the output) to avoid repeated demux overhead.

---

## 9. ASS Karaoke `\k` Tag Pitfalls

### The Sum-Must-Equal-Dialogue-Length Rule

The `\k` tag value is in **centiseconds** and represents the duration of the syllable/word highlight. The sum of all `\k` values on a dialogue line **must equal** the difference between the Dialogue line's end and start timestamps. If the sum is shorter, the final syllable is rendered without highlighting for the remaining time; if longer, the rendering is undefined in most renderers and can cause [libass delay/lag bugs (mpv issue #7121)](https://github.com/mpv-player/mpv/issues/7121).

```
Dialogue: 0,0:00:01.00,0:00:05.58,Default,,0,0,0,,{\k1}{\k42}i{\k33}i{\k20}ta{\k57}ku{\k42} {\k19}i{\k21}i{\k31}e{\k58}na{\k111}i{\k40}
```
Total `\k` sum: 1+42+33+20+57+42+19+21+31+58+111+40 = **475 cs** = 4.75 seconds. Dialogue end − start = 5.58 − 1.00 = 4.58 s = **458 cs**. This line is miscalculated and will exhibit rendering artifacts in libass.

**Fix in your Python export:** After building the `\k` sequence from word timestamps, compute:
```python
line_duration_cs = round((end_time - start_time) * 100)
k_sum = sum(k_values)
if k_sum != line_duration_cs:
    # Distribute residual proportionally to the longest syllables
    delta = line_duration_cs - k_sum
    # Add delta to the last k tag (simplest safe approach)
    k_values[-1] += delta
```

The `\kf` (fill left-to-right) tag follows the same rule and is preferred for karaoke-style highlight since it shows progress through a word. `\ko` (border removal on highlight) is used for pop-up style. For educational subtitles, `\kf` is standard.

---

## 10. Industry Standards: CPS, Line Length, Duration

### Authoritative Reference Table

| Specification | Max CPS | Max chars/line | Max lines | Min duration | Max duration | Gap between events |
|---------------|---------|---------------|-----------|-------------|-------------|-------------------|
| [Netflix (adult)](https://www.gothamlab.com/netflix-subtitle-delivery-requirements-complete-guide/) | **20 CPS** | 42 | 2 | 5/6 s (≈0.833 s) | 7 s | 2 frames |
| [Netflix (children's)](https://www.languagesunlimited.com/netflix-timed-text-style-guide/) | **17 CPS** | 42 | 2 | 5/6 s | 7 s | 2 frames |
| [BBC](https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/) | ~**12 to 15 CPS** (160 to 180 WPM) | Varies | 2 | ~0.3 s/word | n/a | n/a |
| [Academic optimum (Taylor & Francis 2024)](https://www.tandfonline.com/doi/full/10.1080/1475939X.2024.2433259) | **10 to 20 CPS** | n/a | n/a | n/a | n/a | n/a |
| Broadcast/YouTube short-form | 17 to 20 CPS | 42 | 2 | 0.8 s | 6 s | 1 to 2 frames |

**For 2 to 3 word karaoke lines in educational short-form:** A 3-word subtitle displayed for 1.0 s = ~15 chars / 1.0 s = 15 CPS, which is within the 10 to 20 CPS comfort range. At 2.5 words/line average and speaking rate of 130 WPM, expect ~2.3 s per line on average, well within Netflix's 7 s maximum.

**Minimum display time floor:** Enforce ≥ 0.5 s regardless of word count to avoid "flash" perception. From the [ATA subtitle timing principles](https://www.ata-divisions.org/AVD/top-ten-principles-of-subtitle-timing/), subtitles under 2 seconds cause flash perception if the gap between consecutive subtitles is < 11 frames (at 30 fps). Always leave 2 frames (≈66 ms at 30 fps) minimum between subtitle events.

---

## 11. When WhisperX / Demucs Is Worth the Cost

### Decision Matrix

| Condition | Add WhisperX FA? | Add Demucs? | Rationale |
|-----------|-----------------|-------------|-----------|
| Clean studio narration, no music | Optional | No | Whisper word_timestamps=True ± DTW is sufficient; WhisperX FA adds ~20 ms accuracy gain |
| Narration with background music | **Yes** | **Yes** | Music corrupts Whisper and wav2vec2 without separation; Demucs htdemucs_ft is best quality |
| Narration with noise but no music | **Yes** | No | WhisperX FA handles noise better than Whisper timestamps; CrisperWhisper alternative |
| Multiple speakers | **Yes** + diarization | No | WhisperX v3 includes pyannote.audio diarization |
| Non-English narration | **Yes** | Conditional | Check if the target language has a wav2vec2 model; CrisperWhisper currently English-focused |
| Repeated phrases in script | **Yes** | No | FA assigns each instance its own timestamp; fuzzy matching cannot disambiguate |

**Demucs cost (htdemucs_ft):** ~1 to 2× real-time on GPU, ~5 to 8× real-time on CPU for 44.1 kHz stereo. Use lossless WAV/FLAC input. The [2025 Jam-ALT paper](https://arxiv.org/html/2506.15514v1) notes that Demucs `mdx_extra` model outperforms other variants for vocal isolation used as VAD input.

**WhisperX FA overhead:** The alignment stage adds < 10% to total transcription time per [WhisperX paper](https://arxiv.org/html/2303.00747v2), essentially free given the accuracy gain.

---

## 12. QA Metrics and Evaluation Protocol

### Metric Taxonomy

| Metric | What it measures | Target | Reference |
|--------|-----------------|--------|-----------|
| **WER** (per segment) | Token-level transcription accuracy | < 5% for clean narration, < 15% noisy | [Rev ASR Report 2024](https://www.rev.com/resources/asr-benchmark-report) |
| **Mean word boundary error (WBE)** | ms offset between predicted and true word start | < 30 ms (within-collar) | [Rousso et al. arXiv 2406.19363](https://arxiv.org/abs/2406.19363) |
| **Median boundary error** | Robust to outliers from repeated phrases | < 20 ms | Same |
| **F₁ within collar** | % of words within N ms of ground truth | > 0.90 at 20 ms collar | Same |
| **CPS violation rate** | % subtitle events exceeding CPS limit | 0% for professional delivery | Netflix spec |
| **SubER** | Edit distance including timing + segmentation | Lower is better; used at IWSLT | [AppTek SubER explainer](https://www.apptek.ai/post/understanding-the-suber-metric-a-standard-for-automatically-evaluating-subtitle-quality) |
| **Segment boundary MAE** | ms between predicted segment boundary and nearest forced-aligner sentence boundary | < 100 ms | Custom eval |
| **BLEUbr** | BLEU including `<eol>`/`<eob>` break tokens | Context-dependent | [HAL arXiv 2205.09360](https://arxiv.org/abs/2205.09360) |

### Suggested Evaluation Protocol

**Step 1: Ground truth construction.**  
Record a 5-minute narration from your target speaker. Run Montreal Forced Aligner (MFA) on the clean audio + exact transcript to obtain ground-truth word-level boundaries at 10 ms resolution. This is your reference.

**Step 2: Pipeline WER baseline.**  
Run your Whisper + LLM-correction pipeline on the same audio. Compute WER per segment (align ASR hypothesis to reference using `jiwer`). Flag any segment with WER > 20% for manual inspection.

**Step 3: Word boundary error.**  
For each word in the reference, find the predicted word timestamp from WhisperX FA. Compute:
- Mean absolute error (MAE) in ms
- % of words within ±20 ms collar (F₁@20ms)
- % of words within ±50 ms collar (F₁@50ms)

Target: > 80% within ±50 ms for production subtitle quality.

**Step 4: Segment boundary error.**  
For each visual "segment" in your pipeline, record the predicted boundary time (when the new image appears). Find the nearest sentence boundary from the MFA forced alignment. Compute MAE. Target: < 100 ms mean, < 250 ms 95th percentile.

**Step 5: Subtitle QA.**  
Run each generated subtitle event through a CPS checker:
```python
def cps(text, start, end):
    return len(text) / (end - start)
violations = [(t, s, e) for t, s, e in subtitles if cps(t, s, e) > 20]
```
Log minimum event duration < 0.5 s, gap < 66 ms, line > 42 chars.

**Step 6: xfade timing verification.**  
Generate a BITC output (see Section 8) and compute the frame-accurate start time of each segment using `ffprobe -show_frames`. Compare to your pipeline's predicted segment start times. Tolerance: ≤ 1 frame (41.7 ms at 24 fps).

**Step 7: Perceptual AV sync test.**  
Use the [round-trip audio technique](https://blog.paul.cx/post/audio-video-synchronization-with-the-web-audio-api/): play a known click track through the pipeline and measure the offset between the canvas subtitle display event and the audio sample output using the `getOutputTimestamp()` bridge. AV sync error should be < 80 ms (human perceptual threshold during speech).

---

## 13. Architecture Recommendations Summary

```
Audio
  │
  ├─► Silero VAD ──────────────────────────────────────────────────────┐
  │   (segment at silence boundaries, min_silence=100ms)              │
  │                                                                    │
  ├─► Whisper (batched, condition_on_previous_text=False)              │
  │       │                                                            │
  │       ▼                                                            │
  │   LLM word-count-preserving correction                             │
  │       │                                                            │
  │       ▼                                                            │
  │   WhisperX CTC FA  ──── or ────  CrisperWhisper DTW               │
  │   (wav2vec2 + label priors)       (single-model, noisy audio)      │
  │       │                                                            │
  │       ▼                                                            │
  │   [Word timestamps: gold source, do not re-smooth]                │
  │       │                                                            │
  │       ├─► Punctuation restoration (BERT sequence labeler)          │
  │       │                                                            │
  │       ├─► NW/DTW align ASR words → script segment anchors          │
  │       │   (forward-only with confidence gate ≥ 85, fallback→ uniform)│
  │       │                                                            │
  │       ├─► Build karaoke lines (2 to 3 words, syntactic boundary check)│
  │       │   Verify: CPS ≤ 20, min_dur ≥ 0.5s, gap ≥ 66ms            │
  │       │   Build \kf tags: assert sum(\k) == line_duration_cs       │
  │       │                                                            │
  │       └─► Silence-aligned scene cuts (VAD silence windows)         │
  │               │                                                    │
  │               ▼                                                    │
  │           ffmpeg xfade (offset = Σdur[0..i] - Σfd[0..i])          │
  │           + ASS burn (subtitles= filter, UTF-8)                    │
  │           + BITC QA pass                                           │
  │                                                                    │
  └─► Browser preview                                                  │
      ├─► AudioContext + getOutputTimestamp() clock bridge             │
      ├─► rAF delta accumulation (no audio.currentTime)               │
      ├─► Drift correction every ~5 s (single-shot, not continuous)    │
      └─► Subtitle render from immutable word timestamps (no re-snap)  
```

### Key Pitfalls Checklist

- [ ] `refine_word_timestamps` called exactly once, in Python, before export
- [ ] `_snap_subtitle_line_timings` skips words with FA-assigned timestamps
- [ ] `\k` centisecond sum == dialogue end-to-start centiseconds per line
- [ ] ffmpeg xfade offsets use **cumulative** clip duration minus accumulated fade time
- [ ] Subtitle burn uses `subtitles=` filter (not `ass=` which needs different escaping)
- [ ] `condition_on_previous_text=False` enabled in Whisper transcription
- [ ] Silero VAD gates Whisper input (no silence passed to model)
- [ ] CPS ≤ 20 enforced; minimum event duration ≥ 0.5 s
- [ ] Browser clock reads `AudioContext.getOutputTimestamp().performanceTime` not `audio.currentTime`
- [ ] `AudioContext.outputLatency` subtracted for Bluetooth output devices
- [ ] NW/DTW alignment confidence gate at 85+ (RapidFuzz `token_set_ratio`)

---

## Key References

- [WhisperX paper (Bain et al., arXiv 2303.00747)](https://arxiv.org/abs/2303.00747): VAD + FA pipeline
- [CrisperWhisper (Wagner et al., arXiv 2408.16589)](https://arxiv.org/abs/2408.16589): DTW on cross-attention, pause heuristics
- [MFA vs WhisperX comparison (Rousso et al., arXiv 2406.19363)](https://arxiv.org/abs/2406.19363): boundary error benchmarks on TIMIT/Buckeye
- [Less Peaky CTC (Huang et al., ICASSP 2024)](https://ieeexplore.ieee.org/document/10446111/): label priors for CTC FA
- [CTC-Segmentation (Kürzinger et al., arXiv 2007.09127)](https://arxiv.org/abs/2007.09127): long-audio segmentation without perfect transcript
- [Netflix Scene Boundary Detection (TechBlog 2023)](https://netflixtechblog.com/detecting-scene-changes-in-audiovisual-content-77a61d3eaad6): DTW + embeddings for script-to-caption alignment
- [NW subtitle synchronization pipeline (pgdm.ch, 2024)](https://c.pgdm.ch/notes/subtitles-synchronization-1/): Needleman-Wunsch + spline fitting
- [SubER metric (AppTek)](https://www.apptek.ai/post/understanding-the-suber-metric-a-standard-for-automatically-evaluating-subtitle-quality): holistic subtitle QA
- [Sigma segmentation metric (arXiv 2205.09360)](https://arxiv.org/abs/2205.09360): boundary evaluation independent of text quality
- [SBAAM direct subtitle timestamps (arXiv 2405.10741)](https://arxiv.org/abs/2405.10741): SubSONAR metric, end-to-end subtitle timing
- [Web Audio "Tale of Two Clocks" (web.dev)](https://web.dev/articles/audio-scheduling): AudioContext vs JS clock precision
- [AV sync with outputLatency (Jamieonkeys.dev)](https://www.jamieonkeys.dev/posts/web-audio-api-output-latency/): Bluetooth sync fix
- [WebAudio API issue #340 (GitHub)](https://github.com/WebAudio/web-audio-api/issues/340): clock domain bridging
- [xfade offset formula (Stack Overflow)](https://stackoverflow.com/questions/63553906/merging-multiple-video-files-with-ffmpeg-and-xfade-filter): cumulative offset calculation
- [Whisper hallucination mitigations (Reddit production post)](https://www.reddit.com/r/LocalLLaMA/comments/1rlqfd7/): VAD gate, entropy threshold, beam_size=1
- [Jam-ALT Demucs + RMS-VAD (arXiv 2506.15514)](https://arxiv.org/html/2506.15514v1): source separation for boundary improvement
- [Netflix subtitle delivery spec 2026 (Gotham Lab)](https://www.gothamlab.com/netflix-subtitle-delivery-requirements-complete-guide/): CPS, char limits, frame gaps
- [BBC subtitle guidelines](https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/): WPM and timing principles
- [Eye-tracking subtitle segmentation study (PMC 2018)](https://pmc.ncbi.nlm.nih.gov/articles/PMC7901653/): cognitive load of misaligned line breaks
- [ctc-forced-aligner GitHub](https://github.com/MahmoudAshraf97/ctc-forced-aligner): 5× less memory than TorchAudio FA API
- [Whisper has an internal word aligner (arXiv 2509.09987)](https://arxiv.org/html/2509.09987v1): attention-head selection outperforms WhisperX
- [KFX/karaoke basics (jockotan.wordpress.com)](https://jockotan.wordpress.com/2015/08/12/the-basics-of-kfx/): \k tag centisecond semantics
- [Silero VAD vs WebRTC VAD (arXiv 2601.17270)](https://arxiv.org/html/2601.17270v1): window size vs accuracy analysis
