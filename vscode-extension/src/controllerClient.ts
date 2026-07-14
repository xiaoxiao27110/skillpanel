import type {
  ControllerSkill,
  ProcessIds,
  Reconciliation,
  SkillCatalog,
  ToggleResult
} from "./types";

const DEFAULT_TIMEOUT_MS = 15_000;

export class ControllerError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    readonly detail?: unknown,
    readonly currentRevision?: number
  ) {
    super(message);
    this.name = "ControllerError";
  }
}

export interface SkillControllerClient {
  getSkills(): Promise<SkillCatalog>;
  setSkill(name: string, enabled: boolean, expectedRevision: number): Promise<ToggleResult>;
}

export class ControllerClient implements SkillControllerClient {
  constructor(
    private readonly baseUrl = "http://127.0.0.1:8787",
    private readonly timeoutMs = DEFAULT_TIMEOUT_MS,
    private readonly fetchImpl: typeof fetch = globalThis.fetch
  ) {}

  async getSkills(): Promise<SkillCatalog> {
    const value = await this.request("/skills", { method: "GET" });
    return parseCatalog(value);
  }

  async setSkill(
    name: string,
    enabled: boolean,
    expectedRevision: number
  ): Promise<ToggleResult> {
    const value = await this.request(`/skills/${encodeURIComponent(name)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled, expected_revision: expectedRevision })
    });
    return parseToggleResult(value);
  }

  private async request(path: string, init: RequestInit): Promise<unknown> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        ...init,
        headers: { Accept: "application/json", ...init.headers },
        signal: controller.signal
      });
      const body = await readJson(response);
      if (!response.ok) {
        const detail = isRecord(body) ? body.detail : undefined;
        const conflict = isRecord(detail) ? detail.current_revision : undefined;
        const currentRevision = typeof conflict === "number" ? conflict : undefined;
        throw new ControllerError(
          formatHttpError(response.status, detail),
          response.status,
          detail,
          currentRevision
        );
      }
      return body;
    } catch (error) {
      if (error instanceof ControllerError) {
        throw error;
      }
      const message = error instanceof Error ? error.message : String(error);
      throw new ControllerError(`SkillPanel controller request failed: ${message}`);
    } finally {
      clearTimeout(timer);
    }
  }
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) {
    throw new ControllerError(`Controller returned an empty HTTP ${response.status} response`);
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new ControllerError(`Controller returned invalid JSON with HTTP ${response.status}`);
  }
}

function formatHttpError(status: number, detail: unknown): string {
  if (typeof detail === "string") {
    return `Controller returned HTTP ${status}: ${detail}`;
  }
  if (isRecord(detail) && typeof detail.message === "string") {
    return `Controller returned HTTP ${status}: ${detail.message}`;
  }
  return `Controller returned HTTP ${status}`;
}

function parseCatalog(value: unknown): SkillCatalog {
  const data = requireRecord(value, "catalog");
  const revision = requireInteger(data.revision, "catalog.revision");
  const skills = requireArray(data.skills, "catalog.skills").map(parseSkill);
  const reconciliations = requireArray(
    data.reconciliations ?? [],
    "catalog.reconciliations"
  ).map(parseReconciliation);
  return {
    revision,
    skills,
    reconciliations,
    pids: parsePids(data.pids)
  };
}

function parseToggleResult(value: unknown): ToggleResult {
  const data = requireRecord(value, "toggle result");
  const refresh = data.hermes_refresh;
  if (refresh !== undefined && refresh !== "next-turn") {
    throw new ControllerError("Controller returned an invalid hermes_refresh value");
  }
  return {
    ok: requireBoolean(data.ok, "toggle.ok"),
    changed: requireBoolean(data.changed, "toggle.changed"),
    name: requireString(data.name, "toggle.name"),
    enabled: requireBoolean(data.enabled, "toggle.enabled"),
    revision: requireInteger(data.revision, "toggle.revision"),
    reconciliations: requireArray(
      data.reconciliations ?? [],
      "toggle.reconciliations"
    ).map(parseReconciliation),
    opencode_skills: requireArray(data.opencode_skills, "toggle.opencode_skills").map(
      (item) => requireString(item, "toggle.opencode_skills[]")
    ),
    pids: parsePids(data.pids),
    latency_ms: requireNumber(data.latency_ms, "toggle.latency_ms"),
    hermes_refresh: refresh
  };
}

function parseSkill(value: unknown): ControllerSkill {
  const data = requireRecord(value, "skill");
  return {
    name: requireString(data.name, "skill.name"),
    description: requireString(data.description, "skill.description"),
    enabled: requireBoolean(data.enabled, "skill.enabled"),
    location: requireString(data.location, "skill.location")
  };
}

function parseReconciliation(value: unknown): Reconciliation {
  const data = requireRecord(value, "reconciliation");
  return {
    name: requireString(data.name, "reconciliation.name"),
    policy: requireString(data.policy, "reconciliation.policy"),
    action: requireString(data.action, "reconciliation.action"),
    disabled_location: optionalString(data.disabled_location, "reconciliation.disabled_location"),
    quarantine_location: optionalString(
      data.quarantine_location,
      "reconciliation.quarantine_location"
    )
  };
}

function parsePids(value: unknown): ProcessIds {
  const data = requireRecord(value, "pids");
  return {
    opencode: optionalInteger(data.opencode, "pids.opencode"),
    hermes: optionalInteger(data.hermes, "pids.hermes"),
    controller: optionalInteger(data.controller, "pids.controller")
  };
}

function requireRecord(value: unknown, field: string): Record<string, unknown> {
  if (!isRecord(value)) {
    throw new ControllerError(`Controller returned an invalid ${field}`);
  }
  return value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireArray(value: unknown, field: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new ControllerError(`Controller returned an invalid ${field}`);
  }
  return value;
}

function requireString(value: unknown, field: string): string {
  if (typeof value !== "string") {
    throw new ControllerError(`Controller returned an invalid ${field}`);
  }
  return value;
}

function optionalString(value: unknown, field: string): string | undefined {
  return value === undefined ? undefined : requireString(value, field);
}

function requireBoolean(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") {
    throw new ControllerError(`Controller returned an invalid ${field}`);
  }
  return value;
}

function requireInteger(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isInteger(value)) {
    throw new ControllerError(`Controller returned an invalid ${field}`);
  }
  return value;
}

function optionalInteger(value: unknown, field: string): number | null {
  return value === null ? null : requireInteger(value, field);
}

function requireNumber(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new ControllerError(`Controller returned an invalid ${field}`);
  }
  return value;
}

export const testing = { parseCatalog, parseToggleResult };
