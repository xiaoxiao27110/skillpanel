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
    )
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
