import { ControllerError, type SkillControllerClient } from "./controllerClient";
import type { ProjectSkillSource } from "./projectScanner";
import type { ProjectSkill, SkillCatalog } from "./types";

export interface Notifications {
  warning(message: string): void;
  error(message: string): void;
}

export class SkillPanelModel {
  catalog: SkillCatalog | undefined;
  projectSkills: ProjectSkill[] = [];
  pendingName: string | undefined;
  refreshing = false;
  connectionError: string | undefined;

  private readonly listeners = new Set<() => void>();
  private refreshPromise: Promise<void> | undefined;

  constructor(
    private readonly controller: SkillControllerClient,
    private readonly projectSource: ProjectSkillSource,
    private readonly notifications: Notifications
  ) {}

  onDidChange(listener: () => void): { dispose(): void } {
    this.listeners.add(listener);
    return { dispose: () => this.listeners.delete(listener) };
  }

  async refresh(): Promise<void> {
    if (this.pendingName) {
      return;
    }
    if (this.refreshPromise) {
      return this.refreshPromise;
    }
    this.refreshPromise = this.performRefresh();
    try {
      await this.refreshPromise;
    } finally {
      this.refreshPromise = undefined;
    }
  }

  private async performRefresh(): Promise<void> {
    this.refreshing = true;
    this.emit();
    try {
      await this.loadAuthoritativeState();
    } catch (error) {
      this.connectionError = errorMessage(error);
      this.notifications.error(`无法刷新 Skills：${this.connectionError}`);
    } finally {
      this.refreshing = false;
      this.emit();
    }
  }

  async toggle(name: string): Promise<void> {
    if (this.pendingName || this.refreshing || this.connectionError || !this.catalog) {
      return;
    }
    const skill = this.catalog.skills.find((item) => item.name === name);
    if (!skill) {
      return;
    }

    this.pendingName = name;
    this.emit();
    try {
      await this.controller.setSkill(name, !skill.enabled, this.catalog.revision);
    } catch (error) {
      this.notifyToggleFailure(error);
    } finally {
      try {
        await this.loadAuthoritativeState();
      } catch (error) {
        this.connectionError = errorMessage(error);
        this.notifications.error(`无法读取切换后的真实状态：${this.connectionError}`);
      }
      this.pendingName = undefined;
      this.emit();
    }
  }

  private async loadAuthoritativeState(): Promise<void> {
    const [catalog, projectSkills] = await Promise.all([
      this.controller.getSkills(),
      this.projectSource.scan()
    ]);
    this.catalog = catalog;
    this.projectSkills = projectSkills;
    this.connectionError = undefined;
  }

  private notifyToggleFailure(error: unknown): void {
    if (error instanceof ControllerError && error.status === 409) {
      const revision = error.currentRevision;
      this.notifications.warning(
        revision === undefined
          ? "Skill 状态已被其他客户端修改，列表已刷新；请确认后重新点击。"
          : `Skill 状态已更新到 revision ${revision}，列表已刷新；请确认后重新点击。`
      );
      return;
    }
    const status = error instanceof ControllerError ? error.status : undefined;
    const prefix = status === 503 || status === 507 ? "切换失败，控制器已执行回滚" : "切换失败";
    this.notifications.error(`${prefix}：${errorMessage(error)}`);
  }

  private emit(): void {
    for (const listener of this.listeners) {
      listener();
    }
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
