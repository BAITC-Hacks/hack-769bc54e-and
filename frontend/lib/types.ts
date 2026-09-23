export type RunStatus = "running" | "awaiting_approval" | "done" | "failed";
export type StepKind = "thought" | "tool_call" | "tool_result" | "approval" | "final" | "error";

export interface Step {
  id: number;
  kind: StepKind;
  title: string;
  content: Record<string, unknown>;
  at: string;
}

export interface Run {
  id: string;
  task: string;
  status: RunStatus;
  final_report: string;
  created_at: string;
  tokens: { prompt: number; completion: number };
  steps: Step[];
}

export interface Sample {
  id: string;
  label: string;
  task: string;
  input: string;
}

export interface Health {
  ok: boolean;
  mock: boolean;
  /** null в mock-режиме: настоящая модель не используется */
  model: string | null;
  domain: string;
}
