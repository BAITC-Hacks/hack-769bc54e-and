import type { Health, Run, Sample } from "./types";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.error ?? `Сервер ответил ${res.status}`);
  }
  return res.json();
}

export const api = {
  health: () => request<Health>("/health"),
  samples: () => request<Sample[]>("/samples"),
  startRun: (task: string, input: string) =>
    request<Run>("/runs", { method: "POST", body: JSON.stringify({ task, input }) }),
  getRun: (id: string) => request<Run>(`/runs/${id}`),
  decide: (id: string, approve: boolean) =>
    request<{ ok: boolean }>(`/runs/${id}/approve`, { method: "POST", body: JSON.stringify({ approve }) }),
};
