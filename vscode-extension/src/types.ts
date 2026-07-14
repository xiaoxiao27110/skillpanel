export interface ControllerSkill {
  name: string;
  description: string;
  enabled: boolean;
  location: string;
}

export interface Reconciliation {
  name: string;
  policy: string;
  action: string;
  disabled_location?: string;
  quarantine_location?: string;
}

export interface ProcessIds {
  opencode: number | null;
  hermes: number | null;
  controller: number | null;
}

export interface SkillCatalog {
  revision: number;
  skills: ControllerSkill[];
  reconciliations: Reconciliation[];
  pids: ProcessIds;
}

export interface ToggleResult {
  ok: boolean;
  changed: boolean;
  name: string;
  enabled: boolean;
  revision: number;
  reconciliations: Reconciliation[];
  opencode_skills: string[];
  pids: ProcessIds;
  latency_ms: number;
  hermes_refresh?: "next-turn";
}

export interface ProjectSkill {
  name: string;
  description: string;
  sourcePaths: string[];
}

export interface ConflictItem {
  id: string;
  name: string;
  summary: string;
  sourcePaths: string[];
}
