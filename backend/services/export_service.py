import subprocess
import traceback
from pathlib import Path


def _ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    cs = int(round((s % 1) * 100))
    sec = int(s)
    return f"{h}:{m:02d}:{sec:02d}.{cs:02d}"


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

        if has_subtitles and subtitles:
            font_size = editor_state.get("font_size", 48)
            align = editor_state.get("alignment", "bottom")
            align_lower = align.lower() if isinstance(align, str) else "bottom"
            alignment = 2 if align_lower == "bottom" else (5 if align_lower == "center" else 8)
            subtitle_settings = editor_state.get("subtitleSettings", {})
            position_y = subtitle_settings.get("positionY", 88)
            margin_v = int(round(1080 * (1.0 - position_y / 100.0)))
            margin_v = max(10, margin_v)
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
            with open(ass_path, "w", encoding="utf-8") as f:
                f.write("[Script Info]\n")
                f.write("ScriptType: v4.00+\n")
                f.write("PlayResX: 1920\n")
                f.write("PlayResY: 1080\n")
                f.write("ScaledBorderAndShadow: yes\n")
                f.write("\n[V4+ Styles]\n")
                f.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
                f.write(f"Style: Default,Arial,{font_size},{primary_color},{secondary_color},&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline_width},0,{alignment},{margin_v},{margin_v},{margin_v},1\n")
                f.write("\n[Events]\n")
                f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
                for line in subtitles:
                    words = line.get("words", [])
                    if not words:
                        karaoke = line.get("text", "")
                    else:
                        parts = []
                        for idx_w, w in enumerate(words):
                            start = w.get("start", 0)
                            end = w.get("end", start)
                            cs = int(round((end - start) * 100))
                            word_text = w.get("word", "")
                            if idx_w < len(words) - 1:
                                word_text = word_text + " "
                            parts.append(f"{{\\k{cs}}}{word_text}")
                        karaoke = "".join(parts)
                    start_s = line.get("start", 0)
                    end_s = line.get("end", start_s)
                    f.write(f"Dialogue: 0,{_ass_time(start_s)},{_ass_time(end_s)},Default,,0,0,0,,{karaoke}\n")
        else:
            ass_path = None

        cmd = ["ffmpeg", "-y"]
        project_dir_resolved = project_dir.resolve()
        end_padding_seconds = float(editor_state.get("end_padding_seconds", 0.0) or 0.0)
        durations = []
        for idx, seg in enumerate(segments):
            dur = seg.get("end", 0) - seg.get("start", 0)
            if dur <= 0:
                dur = 4.0
            # Add final-frame tail once at export time (silence comes from audio -shortest behavior).
            if idx == len(segments) - 1 and end_padding_seconds > 0:
                dur += end_padding_seconds
            durations.append(dur)
            img_name = seg.get("image", str(len(durations) - 1) + ".png")
            if not img_name.lower().endswith(".png"):
                base = img_name.rsplit(".", 1)[0] if "." in img_name else img_name
                img_name = base + ".png"
            img_path = project_dir / "images" / img_name
            if not img_path.exists():
                alt = project_dir / "images" / (Path(seg["image"]).stem + ".jpg")
                if alt.exists():
                    img_path = alt
                else:
                    alt = project_dir / "images" / (Path(seg["image"]).stem + ".PNG")
                    if alt.exists():
                        img_path = alt
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

        transitions = editor_state.get("transitions", {})
        filter_parts = []
        for i in range(n_images):
            filter_parts.append(
                f"[{i}:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1[v{i}]"
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
        cmd.extend(
            [
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-pix_fmt",
                "yuv420p",
                "-r",
                "30",
                "-shortest",
                out_arg,
            ]
        )

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, cwd=str(project_dir))
        stdout, stderr = proc.communicate()
        stderr_text = stderr.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            print("FFMPEG FAILED:\n", stderr_text)
            job_status["status"] = "error"
            job_status["error"] = stderr_text[-3000:]
        else:
            job_status["status"] = "done"
            job_status["percent"] = 100
    except Exception as e:
        print("EXPORT ERROR:", traceback.format_exc())
        job_status["status"] = "error"
        job_status["error"] = traceback.format_exc()
        return None
