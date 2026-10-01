# MindShift Lab

Automated AI video production pipeline — script to finished MP4, with an in-browser timeline editor in between.

> This README covers setup and running the app. A fuller write-up (architecture, features, screenshots) is coming in a later pass.

## Getting started

**Prerequisites:** Node.js, Python 3.10+, [ffmpeg](https://ffmpeg.org/) on your PATH.

The Next.js app lives at the **repo root** (not in a `frontend/` subfolder — despite the folder being named `frontend` in some older notes, it was flattened to the root early on). Run all `npm` commands from the repo root.

### 1. Backend (FastAPI, port 8050)

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
copy .env.example .env        # macOS/Linux: cp .env.example .env
# edit backend/.env — add your DEEPSEEK_API_KEY (required) and OPENAI_API_KEY (optional)

python -m uvicorn main:app --reload --host 127.0.0.1 --port 8050
```

Check it's up: http://127.0.0.1:8050/docs

### 2. Frontend (Next.js, port 3050)

In a second terminal, from the repo root:

```bash
npm install
npm run dev
```

Open http://localhost:3050

Optional: copy `.env.example` to `.env.local` in the repo root if you're running the backend on a different host/port than the default `http://127.0.0.1:8050`.

### Troubleshooting: "Unable to acquire lock at .next\dev\lock"

Another `next dev` is still running, or a previous run left a stale lock.

1. Stop whatever is using port 3050, or find and kill the process:
   ```powershell
   Get-NetTCPConnection -LocalPort 3050 -ErrorAction SilentlyContinue | Select-Object OwningProcess
   Stop-Process -Id <PID> -Force
   ```
2. Remove the stale lock (safe if no dev server is running): `npm run dev:unlock`
3. If it still fails, delete `.next` entirely (slower next start, but fixes stuck state) and run `npm run dev` again.

## Tech stack

Next.js 16 frontend · FastAPI backend · faster-whisper / WhisperX for transcription & forced alignment · ffmpeg for assembly · DeepSeek / OpenAI for script, prompt, and SEO generation.

## License

MIT — see [LICENSE](LICENSE).
