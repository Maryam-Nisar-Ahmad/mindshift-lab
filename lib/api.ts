import { encodeProjectIdForPath as pid } from '@/lib/projectId';

export const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function listProjects() {
  const res = await fetch(`${API}/api/projects`);
  const data = await res.json();
  if (!res.ok) throw new Error((data as { detail?: string }).detail || 'Request failed');
  return data;
}

export async function createProject(title: string) {
  const res = await fetch(`${API}/api/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error((data as { detail?: string }).detail || 'Request failed');
  return data;
}

export async function getProject(id: string) {
  const res = await fetch(`${API}/api/projects/${pid(id)}`);
  const data = await res.json();
  if (!res.ok) throw new Error((data as { detail?: string }).detail || 'Request failed');
  return data;
}

export async function deleteProject(id: string, confirm_title: string) {
  const res = await fetch(`${API}/api/projects/${pid(id)}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm_title }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error((data as { detail?: string }).detail || 'Request failed');
  return data;
}

export async function updateTitle(id: string, title: string) {
  const res = await fetch(`${API}/api/projects/${pid(id)}/title`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error((data as { detail?: string }).detail || 'Request failed');
  return data;
}

export async function resetStep(id: string, step: number) {
  const res = await fetch(`${API}/api/projects/${pid(id)}/reset-step`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ step }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error((data as { detail?: string }).detail || 'Request failed');
  return data;
}

export async function stepComplete(id: string, step: number, nextStep?: number): Promise<void> {
  const body: { step: number; next_step?: number } = { step };
  if (nextStep !== undefined) body.next_step = nextStep;
  await fetch(`${API}/api/projects/${pid(id)}/step-complete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function getPromptTemplate(name: string) {
  const res = await fetch(`${API}/api/prompts/${name}`);
  const data = await res.json();
  if (!res.ok) throw new Error((data as { detail?: string }).detail || 'Request failed');
  return data;
}

export async function saveScript(id: string, script: string, is_cleaned: boolean) {
  const res = await fetch(`${API}/api/projects/${pid(id)}/script`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ script, is_cleaned }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error((data as { detail?: string }).detail || 'Request failed');
  return data;
}

export async function cleanScript(_id: string, script: string) {
  const base = API.replace(/\/$/, '');
  // Path does not include project id — avoids "Failed to fetch" from malformed URLs; backend ignores id anyway.
  const res = await fetch(`${base}/api/clean-script`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ script }),
    signal: typeof AbortSignal !== 'undefined' && 'timeout' in AbortSignal ? AbortSignal.timeout(300_000) : undefined,
  });
  let data: { detail?: string; cleaned_script?: string } = {};
  try {
    data = (await res.json()) as typeof data;
  } catch {
    throw new Error(res.ok ? 'Invalid response from server' : `Request failed (${res.status})`);
  }
  if (!res.ok) throw new Error(data.detail || 'Request failed');
  return data;
}

export async function generatePrompts(id: string) {
  const res = await fetch(`${API}/api/projects/${pid(id)}/generate-prompts`, {
    method: "POST",
  });
  const data = await res.json();
  if (!res.ok) throw new Error((data as { detail?: string }).detail || 'Request failed');
  return data;
}

export async function getPrompts(id: string) {
  const res = await fetch(`${API}/api/projects/${pid(id)}/prompts`);
  const data = await res.json();
  if (!res.ok) throw new Error((data as { detail?: string }).detail || 'Request failed');
  return data;
}

export async function regeneratePrompt(id: string, segId: string, segment_text: string) {
  const res = await fetch(`${API}/api/projects/${pid(id)}/regenerate-prompt/${encodeURIComponent(segId)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segment_text }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error((data as { detail?: string }).detail || 'Request failed');
  return data;
}

export async function checkImages(id: string) {
  const res = await fetch(`${API}/api/projects/${pid(id)}/image-check`);
  const data = await res.json();
  if (!res.ok) throw new Error((data as { detail?: string }).detail || 'Request failed');
  return data;
}

export async function uploadAudio(id: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API}/api/projects/${pid(id)}/audio`, {
    method: "POST",
    body: form,
  });
  const data = await res.json();
  if (!res.ok) throw new Error((data as { detail?: string }).detail || 'Request failed');
  return data;
}

export async function getAudioStatus(id: string) {
  const res = await fetch(`${API}/api/projects/${pid(id)}/audio-status`);
  const data = await res.json();
  if (!res.ok) throw new Error((data as { detail?: string }).detail || 'Request failed');
  return data;
}

export async function assembleSegments(id: string) {
  const res = await fetch(`${API}/api/projects/${pid(id)}/assemble`, {
    method: "POST",
  });
  const data = await res.json();
  if (!res.ok) throw new Error((data as { detail?: string }).detail || 'Request failed');
  return data;
}

export async function getSegments(id: string) {
  const res = await fetch(`${API}/api/projects/${pid(id)}/segments`);
  const data = await res.json();
  if (!res.ok) throw new Error((data as { detail?: string }).detail || 'Request failed');
  return data;
}

export async function generateSubtitles(id: string) {
  const res = await fetch(`${API}/api/projects/${pid(id)}/generate-subtitles`, {
    method: "POST",
  });
  const data = await res.json();
  if (!res.ok) throw new Error((data as { detail?: string }).detail || 'Request failed');
  return data;
}

export async function saveEditorState(id: string, state: unknown) {
  const res = await fetch(`${API}/api/projects/${pid(id)}/editor-state`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(state),
  });
  const data = await res.json();
  if (!res.ok) throw new Error((data as { detail?: string }).detail || 'Request failed');
  return data;
}

export async function getEditorState(id: string) {
  const res = await fetch(`${API}/api/projects/${pid(id)}/editor-state`);
  const data = await res.json();
  if (!res.ok) throw new Error((data as { detail?: string }).detail || 'Request failed');
  return data;
}

export async function exportVideo(id: string, editorState: unknown) {
  const res = await fetch(`${API}/api/projects/${pid(id)}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ editor_state: editorState }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error((data as { detail?: string }).detail || 'Request failed');
  return data;
}

export async function getJobStatus(jobId: string) {
  const res = await fetch(`${API}/api/jobs/${jobId}`);
  const data = await res.json();
  if (!res.ok) throw new Error((data as { detail?: string }).detail || 'Request failed');
  return data;
}

export function fileUrl(id: string, path: string): string {
  const safePath = path.split("/").map((p) => encodeURIComponent(p)).join("/");
  return `${API}/api/projects/${pid(id)}/files/${safePath}`;
}

export type SeoDataPayload = {
  description: string;
  chapter_timestamps: string;
  tags: string;
  seo_title: string;
  cleaned_script?: string;
};

export async function getSeoData(id: string): Promise<SeoDataPayload> {
  const res = await fetch(`${API}/api/projects/${pid(id)}/seo-data`);
  if (!res.ok)
    return { description: '', chapter_timestamps: '', tags: '', seo_title: '', cleaned_script: '' };
  return res.json();
}

export async function saveSeoData(id: string, data: SeoDataPayload): Promise<void> {
  await fetch(`${API}/api/projects/${pid(id)}/seo-data`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
}
