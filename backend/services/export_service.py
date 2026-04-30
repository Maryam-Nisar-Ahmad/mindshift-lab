import subprocess
import traceback
from pathlib import Path

from .sidecar_subtitles import write_srt, write_vtt
from .subtitle_qc import validate_subtitle_lines
from .segment_images import resolve_image_path_under_dir


# High quality: source images are sharp; default libx264 is too soft for stills.
X264_CRF = "16"
X264_PRESET = "slow"
EXPORT_FPS = 30


def _shift_subtitle_lines(lines: list[dict], offset_sec: float) -> list[dict]:
    out: list[dict] = []
    for line in lines:
        st = float(line.get("start", 0) or 0) + offset_sec
        en = float(line.get("end", st) or st) + offset_sec
        ws = []
        for w in line.get("words") or []:
            ws.append(
                {
                    "word": w.get("word", ""),
                    "start": float(w.get("start", 0) or 0) + offset_sec,
                    "end": float(w.get("end", 0) or 0) + offset_sec,
                }
            )
        out.append({**line, "start": st, "end": en, "words": ws})
    return out


def _ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    cs = int(round((s % 1) * 100))
    sec = int(s)
    return f"{h}:{m:02d}:{sec:02d}.{cs:02d}"


def _normalize_ass_k_centis(words: list, line_start: float, line_end: float) -> list[int]:
    """ASS \\k durations (centiseconds) must sum to the dialogue event length or karaoke drifts."""
    total_target = max(1, int(round((line_end - line_start) * 100)))
    if not words:
        return []
    raw: list[int] = []
    for w in words:
        ws = float(w.get("start", line_start))
        we = float(w.get("end", ws))
        ws = max(line_start, min(ws, line_end))
        we = max(ws, min(we, line_end))
        raw.append(max(1, int(round((we - ws) * 100))))
    s = sum(raw)
    if s == total_target:
        return raw
    # Avoid rescaling when word timings already match the cue within rounding (keeps lip-sync tighter).
    if abs(s - total_target) <= 2 and s > 0:
        out = list(raw)
        out[-1] = max(1, out[-1] + (total_target - s))
        return out
    if s <= 0:
        return [total_target]
    scaled = [max(1, int(round(c * total_target / s))) for c in raw]
    drift = total_target - sum(scaled)
    scaled[-1] = max(1, scaled[-1] + drift)
    return scaled


def build_and_export(project_id, project_dir, editor_state, job_status):
    try:
        project_dir = Path(project_dir)
        segments = editor_state.get("segments", [])
        has_subtitles = editor_state.get("has_subtitles", False)
        subtitles = editor_state.get("subtitles", [])
        if not segments:
            seg_path = project_dir / "segments.json"
            if seg_path.exists():
                import json
                with open(seg_path, "r", encoding="utf-8") as f:
                    segments = json.load(f)

        offset_sec = float(editor_state.get("subtitle_offset_ms", 0) or 0) / 1000.0
        shifted_subs = _shift_subtitle_lines(subtitles, offset_sec) if subtitles else []

        if has_subtitles and subtitles:
            qc_ok, qc_errs = validate_subtitle_lines(shifted_subs)
            if not qc_ok:
                # User may still export after manual review — surface warnings but do not block ffmpeg.
                job_status["subtitle_qc_warnings"] = qc_errs[:24]
            subs_for_write = shifted_subs
            font_size = editor_state.get("font_size", 48)
            align = editor_state.get("alignment", "bottom")
            align_lower = align.lower() if isinstance(align, str) else "bottom"
            alignment = 2 if align_lower == "bottom" else (5 if align_lower == "center" else 8)
            subtitle_settings = editor_state.get("subtitleSettings", {})
            position_y = subtitle_settings.get("positionY", 88)
            margin_v = int(round(1080 * (1.0 - position_y / 100.0)))
            margin_v = max(54, margin_v, 10)
            margin_lr = 96
            highlight_enabled = subtitle_settings.get("highlight_enabled", True)
            highlight_hex = subtitle_settings.get("highlight_color", "#FFFF00")
            base_hex = subtitle_settings.get("color", "#FFFFFF")
            outline_width = subtitle_settings.get("outline_width", 4)

            def hex_to_ass(h):
                h = h.lstrip("#")
                r, g, b = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
                return f"&H00{b:02X}{g:02X}{r:02X}"

            primary_color = hex_to_ass(highlight_hex) if highlight_enabled else hex_to_ass(base_hex)
            secondary_color = hex_to_ass(base_hex)
            ass_path = project_dir / "subtitles.ass"
            subs_lines = subs_for_write
            with open(ass_path, "w", encoding="utf-8") as f:
                f.write("[Script Info]\n")
                f.write("ScriptType: v4.00+\n")
                f.write("PlayResX: 1920\n")
                f.write("PlayResY: 1080\n")
                f.write("ScaledBorderAndShadow: yes\n")
                f.write("\n[V4+ Styles]\n")
                f.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
                outline_ass = min(max(int(outline_width or 2), 2), 5)
                # ASS BorderStyle 3 draws an opaque box; 1 = outline only (matches editor preview).
                f.write(
                    f"Style: Default,Arial,{font_size},{primary_color},{secondary_color},&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline_ass},0,{alignment},{margin_lr},{margin_lr},{margin_v},1\n"
                )
                f.write("\n[Events]\n")
                f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
                for line in subs_lines:
                    start_s = float(line.get("start", 0) or 0)
                    end_s = float(line.get("end", start_s) or start_s)
                    words = line.get("words", [])
                    if not words:
                        karaoke = line.get("text", "")
                    else:
                        k_list = _normalize_ass_k_centis(words, start_s, end_s)
                        parts = []
                        for idx_w, w in enumerate(words):
                            cs = k_list[idx_w] if idx_w < len(k_list) else 1
                            word_text = w.get("word", "")
                            if idx_w < len(words) - 1:
                                word_text = word_text + " "
                            parts.append(f"{{\\k{cs}}}{word_text}")
                        karaoke = "".join(parts)
                    f.write(f"Dialogue: 0,{_ass_time(start_s)},{_ass_time(end_s)},Default,,0,0,0,,{karaoke}\n")
            write_srt(project_dir / "subtitles_upload.srt", subs_lines, offset_sec=0.0)
            write_vtt(project_dir / "subtitles_upload.vtt", subs_lines, offset_sec=0.0)
            try:
                import json as _json

                mp = project_dir / "meta.json"
                title_txt = ""
                if mp.exists():
                    title_txt = (_json.loads(mp.read_text(encoding="utf-8"))).get("title", "")
                (project_dir / "publish_bundle.json").write_text(
                    _json.dumps(
                        {"project_id": project_id, "title": title_txt, "exported_mp4": "output.mp4"},
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass
        else:
            ass_path = None

        cmd = ["ffmpeg", "-y"]
        project_dir_resolved = project_dir.resolve()
        end_padding_seconds = float(editor_state.get("end_padding_seconds", 0.0) or 0.0)
        transitions = editor_state.get("transitions", {})
        durations = []
        for idx, seg in enumerate(segments):
            end_t = float(seg.get("end", 0) or 0)
            start_t = float(seg.get("start", 0) or 0)
            # Option B hold: image i stays on screen until image i+1 starts.
            # This guarantees no black frames during inter-line narration pauses.
            clip_start = 0.0 if idx == 0 else start_t
            if idx < len(segments) - 1:
                next_start = float(segments[idx + 1].get("start", end_t) or end_t)
                dur = next_start - clip_start
            else:
                dur = end_t - clip_start
            if dur <= 0:
                dur = 4.0
            # xfade overlaps clips by fade duration, which shortens the output vs the editor timeline.
            # Extend each still after the first by the incoming fade length so total time matches preview.
            if idx > 0:
                trans_key = str(idx - 1) + "-" + str(idx)
                prev_trans = transitions.get(trans_key, {})
                if prev_trans.get("type") == "fade":
                    dur += float(prev_trans.get("duration", 0.5) or 0.0)
            # Add final-frame tail once at export time (matches preview tail past last segment end).
            if idx == len(segments) - 1 and end_padding_seconds > 0:
                dur += end_padding_seconds
            durations.append(dur)
            images_dir = project_dir / "images"
            sid_raw = seg.get("segment_id", idx + 1)
            try:
                sid_int = int(sid_raw)
            except (TypeError, ValueError):
                sid_int = idx + 1
            img_path = resolve_image_path_under_dir(images_dir, sid_int, seg.get("image"))
            if img_path is None:
                fallback = seg.get("image") or f"{sid_int}.png"
                img_path = images_dir / Path(str(fallback)).name
            try:
                in_arg = img_path.resolve().relative_to(project_dir_resolved).as_posix()
            except ValueError:
                in_arg = str(img_path.resolve())
            cmd.extend(["-loop", "1", "-t", str(dur), "-i", in_arg])

        audio_path = project_dir / "audio.mp3"
        try:
            audio_in = audio_path.resolve().relative_to(project_dir_resolved).as_posix()
        except ValueError:
            audio_in = str(audio_path.resolve())
        cmd.extend(["-i", audio_in])
        n_images = len(segments)
        audio_idx = n_images

        filter_parts = []
        for i in range(n_images):
            filter_parts.append(
                f"[{i}:v]scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1[v{i}]"
            )
        prev_label = "v0"
        accumulated_duration = durations[0]
        total_fade_reduction = 0.0
        for i in range(1, n_images):
            trans_key = str(i - 1) + "-" + str(i)
            trans = transitions.get(trans_key, {"type": "none"})
            out_label = f"xf{i}"
            if trans.get("type") == "fade":
                fade_dur = trans.get("duration", 0.5)
                offset = round(accumulated_duration - total_fade_reduction - fade_dur, 3)
                offset = max(0.1, offset)
                filter_parts.append(
                    f"[{prev_label}][v{i}]xfade=transition=fade:duration={fade_dur}:offset={offset}[{out_label}]"
                )
                total_fade_reduction += fade_dur
            else:
                filter_parts.append(f"[{prev_label}][v{i}]concat=n=2:v=1:a=0[{out_label}]")
            accumulated_duration += durations[i]
            prev_label = out_label
        final_video = f"[{prev_label}]" if n_images > 1 else "[v0]"

        output_path = project_dir / "output.mp4"
        if ass_path is not None and ass_path.exists():
            filter_parts.append(f"[{prev_label}]ass=subtitles.ass[vout]")
            final_video = "[vout]"

        filter_complex = ";".join(filter_parts)
        filter_script_path = project_dir / "_export_filter_complex.txt"
        filter_script_path.write_text(filter_complex, encoding="utf-8")
        cmd.extend(
            [
                "-filter_complex_script",
                filter_script_path.name,
                "-map",
                final_video,
                "-map",
                f"{audio_idx}:a",
            ]
        )
        try:
            out_arg = output_path.resolve().relative_to(project_dir_resolved).as_posix()
        except ValueError:
            out_arg = str(output_path.resolve())

        # Compute a safe output duration floor from all active timelines so subtitles/audio
        # are not truncated when scene timings drift early.
        timeline_floor = 0.0
        if segments:
            timeline_floor = max(timeline_floor, max(float(s.get("end", 0) or 0) for s in segments) + end_padding_seconds)
        if shifted_subs:
            timeline_floor = max(timeline_floor, max(float(line.get("end", 0) or 0) for line in shifted_subs))

        out_duration = editor_state.get("totalDuration")
        try:
            out_duration = float(out_duration) if out_duration is not None else None
        except (TypeError, ValueError):
            out_duration = None
        if out_duration is None or out_duration <= 0:
            out_duration = timeline_floor if timeline_floor > 0 else None
        elif timeline_floor > 0:
            out_duration = max(out_duration, timeline_floor)

        tail_opts: list = [
            "-c:v",
            "libx264",
            "-preset",
            X264_PRESET,
            "-profile:v",
            "high",
            "-level:v",
            "4.1",
            "-crf",
            X264_CRF,
            "-tune",
            "stillimage",
            "-bf",
            "2",
            "-g",
            str(EXPORT_FPS),
            "-keyint_min",
            str(max(15, EXPORT_FPS // 2)),
            "-sc_threshold",
            "0",
            "-c:a",
            "aac",
            "-b:a",
            "384k",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(EXPORT_FPS),
            "-fps_mode",
            "cfr",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_range",
            "tv",
            "-movflags",
            "+faststart",
        ]
        if out_duration is not None and out_duration > 0:
            tail_opts.extend(["-t", str(out_duration)])
        else:
            tail_opts.append("-shortest")
        tail_opts.append(out_arg)
        cmd.extend(tail_opts)

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, cwd=str(project_dir))
        stdout, stderr = proc.communicate()
        stderr_text = stderr.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            print("FFMPEG FAILED:\n", stderr_text)
            job_status["status"] = "error"
            job_status["error"] = stderr_text[-3000:]
        else:
            try:
                pr = subprocess.run(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration,size",
                        "-of",
                        "json",
                        str(output_path.resolve()),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if pr.returncode == 0 and pr.stdout:
                    import json as _json

                    info = _json.loads(pr.stdout)
                    dur = float(info.get("format", {}).get("duration", 0) or 0)
                    if dur <= 0.1:
                        job_status["status"] = "error"
                        job_status["error"] = "Export file has zero duration"
                        return None
            except Exception as ex:
                print("ffprobe post-check:", ex)
            job_status["status"] = "done"
            job_status["percent"] = 100
    except Exception as e:
        print("EXPORT ERROR:", traceback.format_exc())
        job_status["status"] = "error"
        job_status["error"] = traceback.format_exc()
        return None
