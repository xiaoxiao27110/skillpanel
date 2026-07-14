import * as vscode from "vscode";
import type { SkillPanelModel } from "./skillPanelModel";
import type { ConflictItem, ControllerSkill, ProjectSkill } from "./types";
import { buildConflicts } from "./viewModel";

type GroupKind = "enabled" | "disabled" | "project" | "conflict";

interface GroupNode {
  kind: "group";
  group: GroupKind;
  count: number;
}

interface GlobalNode {
  kind: "global";
  skill: ControllerSkill;
}

interface ProjectNode {
  kind: "project";
  skill: ProjectSkill;
}

interface ConflictNode {
  kind: "conflict";
  conflict: ConflictItem;
}

interface StatusNode {
  kind: "empty" | "error";
}

export type SkillTreeNode = GroupNode | GlobalNode | ProjectNode | ConflictNode | StatusNode;

export class SkillTreeProvider implements vscode.TreeDataProvider<SkillTreeNode> {
  private readonly changed = new vscode.EventEmitter<SkillTreeNode | undefined>();
  readonly onDidChangeTreeData = this.changed.event;

  constructor(private readonly model: SkillPanelModel) {}

  refresh(): void {
    this.changed.fire(undefined);
  }

  getTreeItem(node: SkillTreeNode): vscode.TreeItem {
    switch (node.kind) {
      case "group":
        return this.groupItem(node);
      case "global":
        return this.globalItem(node.skill);
      case "project":
        return this.projectItem(node.skill);
      case "conflict":
        return this.conflictItem(node.conflict);
      case "empty":
        return this.emptyItem();
      case "error":
        return this.errorItem();
    }
  }

  getChildren(node?: SkillTreeNode): SkillTreeNode[] {
    if (!node) {
      return this.rootNodes();
    }
    if (node.kind !== "group") {
      return [];
    }
    const catalog = this.model.catalog;
    switch (node.group) {
      case "enabled":
        return (catalog?.skills ?? [])
          .filter((skill) => skill.enabled)
          .sort(compareNames)
          .map((skill) => ({ kind: "global", skill }));
      case "disabled":
        return (catalog?.skills ?? [])
          .filter((skill) => !skill.enabled)
          .sort(compareNames)
          .map((skill) => ({ kind: "global", skill }));
      case "project":
        return [...this.model.projectSkills]
          .sort(compareNames)
          .map((skill) => ({ kind: "project", skill }));
      case "conflict":
        return buildConflicts(this.model.catalog, this.model.projectSkills).map((conflict) => ({
          kind: "conflict",
          conflict
        }));
    }
  }

  private rootNodes(): SkillTreeNode[] {
    const result: SkillTreeNode[] = [];
    const catalog = this.model.catalog;
    if (this.model.connectionError || !catalog) {
      result.push({ kind: "error" });
    }
    if (catalog?.skills.length === 0 && !this.model.connectionError) {
      result.push({ kind: "empty" });
    } else if (catalog?.skills.length) {
      result.push(
        {
          kind: "group",
          group: "enabled",
          count: catalog.skills.filter((skill) => skill.enabled).length
        },
        {
          kind: "group",
          group: "disabled",
          count: catalog.skills.filter((skill) => !skill.enabled).length
        }
      );
    }

    if (this.model.projectSkills.length > 0) {
      result.push({ kind: "group", group: "project", count: this.model.projectSkills.length });
    }
    const conflicts = buildConflicts(catalog, this.model.projectSkills);
    if (conflicts.length > 0) {
      result.push({ kind: "group", group: "conflict", count: conflicts.length });
    }
    return result;
  }

  private groupItem(node: GroupNode): vscode.TreeItem {
    const labels: Record<GroupKind, string> = {
      enabled: "已启用",
      disabled: "已禁用",
      project: "项目级",
      conflict: "冲突"
    };
    const item = new vscode.TreeItem(labels[node.group], vscode.TreeItemCollapsibleState.Expanded);
    item.id = `group:${node.group}`;
    item.description = String(node.count);
    item.contextValue = `skillPanel.group.${node.group}`;
    return item;
  }

  private globalItem(skill: ControllerSkill): vscode.TreeItem {
    const pending = this.model.pendingName === skill.name;
    const item = new vscode.TreeItem(skill.name, vscode.TreeItemCollapsibleState.None);
    item.id = `global:${skill.name}`;
    item.description = pending ? "切换中…" : undefined;
    item.contextValue = pending ? "skillPanel.global.pending" : "skillPanel.global";
    item.iconPath = pending
      ? new vscode.ThemeIcon("loading~spin")
      : skill.enabled
        ? new vscode.ThemeIcon("circle-filled", new vscode.ThemeColor("charts.green"))
        : new vscode.ThemeIcon("circle-outline", new vscode.ThemeColor("disabledForeground"));
    item.tooltip = skillTooltip(skill);
    item.accessibilityInformation = {
      role: "button",
      label: pending
        ? `${skill.name}，正在切换`
        : `${skill.name}，${skill.enabled ? "已启用，点击禁用" : "已禁用，点击启用"}`
    };
    if (!this.model.pendingName && !this.model.refreshing && !this.model.connectionError) {
      item.command = {
        command: "skillPanel.toggleSkill",
        title: skill.enabled ? "禁用 Skill" : "启用 Skill",
        arguments: [skill.name]
      };
    }
    return item;
  }

  private projectItem(skill: ProjectSkill): vscode.TreeItem {
    const item = new vscode.TreeItem(skill.name, vscode.TreeItemCollapsibleState.None);
    item.id = `project:${skill.name}`;
    item.contextValue = "skillPanel.project";
    item.iconPath = new vscode.ThemeIcon("circle-filled", new vscode.ThemeColor("charts.blue"));
    item.tooltip = projectTooltip(skill);
    item.accessibilityInformation = {
      role: "treeitem",
      label: `${skill.name}，项目级，始终启用`
    };
    return item;
  }

  private conflictItem(conflict: ConflictItem): vscode.TreeItem {
    const item = new vscode.TreeItem(conflict.name, vscode.TreeItemCollapsibleState.None);
    item.id = `conflict:${conflict.id}`;
    item.description = conflict.summary;
    item.contextValue = "skillPanel.conflict";
    item.iconPath = new vscode.ThemeIcon(
      "warning",
      new vscode.ThemeColor("problemsWarningIcon.foreground")
    );
    item.tooltip = linesTooltip([conflict.name, conflict.summary, ...conflict.sourcePaths]);
    return item;
  }

  private emptyItem(): vscode.TreeItem {
    const item = new vscode.TreeItem("未发现全局 Skills", vscode.TreeItemCollapsibleState.None);
    item.id = "status:empty";
    item.description = "点击刷新";
    item.iconPath = new vscode.ThemeIcon("info");
    item.tooltip = linesTooltip([
      "未发现全局 Skills",
      "启用目录：/root/.config/opencode/skills",
      "禁用目录：/root/.config/opencode/skills-disabled"
    ]);
    item.command = { command: "skillPanel.refresh", title: "刷新 Skills" };
    return item;
  }

  private errorItem(): vscode.TreeItem {
    const item = new vscode.TreeItem("无法连接 SkillPanel 控制器", vscode.TreeItemCollapsibleState.None);
    item.id = "status:error";
    item.description = "点击重试";
    item.iconPath = new vscode.ThemeIcon(
      "error",
      new vscode.ThemeColor("problemsErrorIcon.foreground")
    );
    item.tooltip = linesTooltip([
      this.model.connectionError ?? "尚未读取控制器状态",
      "控制器地址：http://127.0.0.1:8787"
    ]);
    item.command = { command: "skillPanel.refresh", title: "刷新 Skills" };
    return item;
  }
}

export function panelMessage(_model: SkillPanelModel): string {
  return "开关成功后，从下一轮对话开始生效";
}

function skillTooltip(skill: ControllerSkill): vscode.MarkdownString {
  return linesTooltip([
    skill.name,
    skill.description || "无描述",
    `状态：${skill.enabled ? "已启用" : "已禁用"}`,
    `来源：${skill.location}`
  ]);
}

function projectTooltip(skill: ProjectSkill): vscode.MarkdownString {
  return linesTooltip([
    skill.name,
    skill.description,
    "状态：项目级，始终启用",
    ...skill.sourcePaths.map((source) => `来源：${source}`)
  ]);
}

function linesTooltip(lines: readonly string[]): vscode.MarkdownString {
  const tooltip = new vscode.MarkdownString();
  tooltip.isTrusted = false;
  tooltip.supportHtml = false;
  lines.forEach((line, index) => {
    if (index > 0) {
      tooltip.appendMarkdown("\n\n");
    }
    tooltip.appendText(line);
  });
  return tooltip;
}

function compareNames(left: { name: string }, right: { name: string }): number {
  return left.name.localeCompare(right.name);
}

export const testing = { linesTooltip, skillTooltip, projectTooltip };
