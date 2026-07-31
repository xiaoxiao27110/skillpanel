import * as vscode from "vscode";
import { ControllerClient } from "./controllerClient";
import { ProjectSkillScanner } from "./projectScanner";
import { SkillPanelModel } from "./skillPanelModel";
import { panelMessage, SkillTreeProvider } from "./treeProvider";

export interface SkillPanelExtensionApi {
  readonly model: SkillPanelModel;
  readonly provider: SkillTreeProvider;
  refresh(): Promise<void>;
}

export function activate(context: vscode.ExtensionContext): SkillPanelExtensionApi {
  const controllerUrl = process.env.SKILLPANEL_CONTROLLER_URL ?? "http://127.0.0.1:8787";
  const model = new SkillPanelModel(
    new ControllerClient(controllerUrl),
    new ProjectSkillScanner(),
    {
      warning: (message) => void vscode.window.showWarningMessage(message),
      error: (message) => void vscode.window.showErrorMessage(message)
    }
  );
  const provider = new SkillTreeProvider(model);
  const treeView = vscode.window.createTreeView("skillPanel.skills", {
    treeDataProvider: provider,
    showCollapseAll: false
  });

  const updateView = (): void => {
    provider.refresh();
    treeView.message = panelMessage(model);
    void vscode.commands.executeCommand(
      "setContext",
      "skillPanel.hasGlobalSkills",
      (model.catalog?.skills.length ?? 0) > 0
    );
  };

  context.subscriptions.push(
    treeView,
    model.onDidChange(updateView),
    vscode.commands.registerCommand("skillPanel.refresh", () => model.refresh()),
    vscode.commands.registerCommand("skillPanel.toggleSkill", (name: unknown) =>
      typeof name === "string" ? model.toggle(name) : undefined
    ),
    vscode.commands.registerCommand("skillPanel.activateScene", (name: unknown) =>
      typeof name === "string" ? model.activateScene(name) : undefined
    ),
    vscode.commands.registerCommand("skillPanel.createScene", () => promptCreateScene(model)),
    vscode.commands.registerCommand("skillPanel.renameScene", (target: unknown) => {
      const name = sceneNameFrom(target);
      return name === undefined ? undefined : promptRenameScene(model, name);
    }),
    vscode.commands.registerCommand("skillPanel.deleteScene", (target: unknown) => {
      const name = sceneNameFrom(target);
      return name === undefined ? undefined : confirmDeleteScene(model, name);
    })
  );

  updateView();
  void model.refresh();

  return {
    model,
    provider,
    refresh: () => model.refresh()
  };
}

export function deactivate(): void {}

function sceneNameFrom(target: unknown): string | undefined {
  if (typeof target === "string") {
    return target;
  }
  if (
    target instanceof vscode.TreeItem &&
    typeof target.id === "string" &&
    target.id.startsWith("scene:")
  ) {
    return target.id.slice("scene:".length);
  }
  return undefined;
}

function validateSceneName(
  model: SkillPanelModel,
  value: string,
  original?: string
): string | undefined {
  const name = value.trim();
  if (!name) {
    return "场景名称不能为空";
  }
  if (name !== original && model.scenes?.scenes.some((scene) => scene.name === name)) {
    return "已存在同名场景";
  }
  return undefined;
}

async function promptCreateScene(model: SkillPanelModel): Promise<void> {
  const name = await vscode.window.showInputBox({
    title: "新建场景",
    prompt: "创建后立即切换到该场景，并启用全部 Skill",
    placeHolder: "场景名称",
    validateInput: (value) => validateSceneName(model, value)
  });
  if (name === undefined) {
    return;
  }
  await model.createScene(name.trim());
}

async function promptRenameScene(model: SkillPanelModel, name: string): Promise<void> {
  const newName = await vscode.window.showInputBox({
    title: "重命名场景",
    prompt: `为场景「${name}」输入新名称`,
    value: name,
    validateInput: (value) => validateSceneName(model, value, name)
  });
  if (newName === undefined) {
    return;
  }
  const trimmed = newName.trim();
  if (trimmed === name) {
    return;
  }
  await model.renameScene(name, trimmed);
}

async function confirmDeleteScene(model: SkillPanelModel, name: string): Promise<void> {
  const active = model.scenes?.active === name;
  const choice = await vscode.window.showWarningMessage(
    active
      ? `确定删除场景「${name}」？删除当前激活场景会自动切换到其他场景。`
      : `确定删除场景「${name}」？`,
    { modal: true },
    "删除"
  );
  if (choice !== "删除") {
    return;
  }
  await model.deleteScene(name);
}
