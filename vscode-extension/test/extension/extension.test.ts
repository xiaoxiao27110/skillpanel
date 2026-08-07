import * as assert from "node:assert/strict";
import * as fs from "node:fs/promises";
import * as http from "node:http";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";
import type { SkillPanelExtensionApi } from "../../src/extension";
import { ProjectSkillScanner } from "../../src/projectScanner";
import { panelMessage } from "../../src/treeProvider";

let server: http.Server;
let api: SkillPanelExtensionApi;
let revision = 40;
let sceneRevision = 2;
let enabled = true;

function sceneCatalog() {
  return {
    revision: sceneRevision,
    active: "默认",
    scenes: [
      { name: "默认", disabled: [], active: true },
      { name: "写作", disabled: ["global-only"], active: false }
    ],
    pids: { opencode: 10, hermes: 11, codex: 13, controller: 12 }
  };
}

function catalog() {
  return {
    revision,
    skills: [
      {
        name: "canary-alpha",
        description: "Global canary with [untrusted](command:evil) text",
        enabled,
        location: enabled ? "/enabled/canary-alpha" : "/disabled/canary-alpha"
      },
      {
        name: "global-only",
        description: "Only global",
        enabled: false,
        location: "/disabled/global-only"
      }
    ],
    reconciliations: [
      {
        name: "quarantined",
        policy: "disabled-wins",
        action: "quarantined-enabled-copy",
        disabled_location: "/disabled/quarantined",
        quarantine_location: "/disabled/.skillpanel-conflicts/quarantined/1"
      }
    ],
    pids: { opencode: 10, hermes: 11, codex: 13, controller: 12 }
  };
}

suite("SkillPanel extension", () => {
  suiteSetup(async () => {
    server = http.createServer((request, response) => {
      const send = (status: number, value: unknown) => {
        response.writeHead(status, { "Content-Type": "application/json" });
        response.end(JSON.stringify(value));
      };
      if (request.method === "GET" && request.url === "/skills") {
        send(200, catalog());
        return;
      }
      if (request.method === "GET" && request.url === "/scenes") {
        send(200, sceneCatalog());
        return;
      }
      if (request.method === "PUT" && request.url === "/skills/canary-alpha") {
        let raw = "";
        request.setEncoding("utf8");
        request.on("data", (chunk) => {
          raw += chunk;
        });
        request.on("end", () => {
          const body = JSON.parse(raw) as { enabled: boolean; expected_revision: number };
          if (body.expected_revision !== revision) {
            send(409, { detail: { message: "Revision conflict", current_revision: revision } });
            return;
          }
          enabled = body.enabled;
          revision += 1;
          send(200, {
            ok: true,
            changed: true,
            name: "canary-alpha",
            enabled,
            revision,
            reconciliations: catalog().reconciliations,
            hermes_refresh: "next-turn",
            opencode_skills: enabled ? ["canary-alpha"] : [],
            pids: catalog().pids,
            latency_ms: 1,
            active_scene: "默认",
            scenes_revision: sceneRevision
          });
        });
        return;
      }
      send(404, { detail: "not found" });
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    assert.ok(address && typeof address === "object");
    process.env.SKILLPANEL_CONTROLLER_URL = `http://127.0.0.1:${address.port}`;

    const extension = vscode.extensions.getExtension<SkillPanelExtensionApi>("skillpanel.skill-panel");
    assert.ok(extension, "SkillPanel extension was not discovered");
    api = await extension.activate();
    await api.refresh();
  });

  suiteTeardown(async () => {
    delete process.env.SKILLPANEL_CONTROLLER_URL;
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve()))
    );
  });

  test("runs as a workspace extension and registers only the intended commands", async () => {
    const extension = vscode.extensions.getExtension("skillpanel.skill-panel");
    assert.deepEqual(extension?.packageJSON.extensionKind, ["workspace"]);
    assert.deepEqual(extension?.packageJSON.activationEvents, ["onView:skillPanel.skills"]);
    assert.deepEqual(
      extension?.packageJSON.contributes.commands.map((entry: { command: string }) => entry.command),
      ["skillPanel.refresh", "skillPanel.createScene", "skillPanel.renameScene", "skillPanel.deleteScene"]
    );
    assert.deepEqual(
      extension?.packageJSON.contributes.viewsContainers.activitybar.map(
        (entry: { id: string }) => entry.id
      ),
      ["skillPanel"]
    );
    assert.deepEqual(
      extension?.packageJSON.contributes.views.skillPanel.map((entry: { id: string }) => entry.id),
      ["skillPanel.skills"]
    );
    assert.equal(extension?.packageJSON.contributes.menus.commandPalette, undefined);
    const commands = await vscode.commands.getCommands(true);
    assert.deepEqual(
      commands.filter((command) => /^skillPanel\.(?!skills\.)/.test(command)).sort(),
      [
        "skillPanel.activateScene",
        "skillPanel.createScene",
        "skillPanel.deleteScene",
        "skillPanel.refresh",
        "skillPanel.renameScene",
        "skillPanel.toggleSkill"
      ]
    );
  });

  test("builds native tree groups, icons, tooltips, and read-only project rows", () => {
    const roots = api.provider.getChildren();
    const groups = roots.filter((node) => node.kind === "group");
    assert.deepEqual(
      groups.map((node) => (node.kind === "group" ? node.group : "")),
      ["enabled", "disabled", "project", "conflict"]
    );

    const enabledGroup = groups.find((node) => node.kind === "group" && node.group === "enabled");
    assert.ok(enabledGroup);
    const globalNode = api.provider.getChildren(enabledGroup)[0];
    const globalItem = api.provider.getTreeItem(globalNode);
    assert.equal(globalItem.id, "global:canary-alpha");
    assert.equal(globalItem.command?.command, "skillPanel.toggleSkill");
    assert.equal((globalItem.iconPath as vscode.ThemeIcon).id, "circle-filled");
    assert.equal((globalItem.iconPath as vscode.ThemeIcon).color?.id, "charts.green");
    const tooltip = globalItem.tooltip as vscode.MarkdownString;
    const tooltipText = tooltip.value.replaceAll("&nbsp;", " ");
    assert.equal(tooltip.isTrusted, false);
    assert.equal(tooltip.supportHtml, false);
    assert.match(tooltipText, /canary-alpha/);
    assert.match(tooltipText, /Global canary with/);
    assert.match(tooltipText, /状态：已启用/);
    assert.match(tooltipText, /来源：\/enabled\/canary-alpha/);
    assert.match(tooltipText, /command:evil/);
    assert.doesNotMatch(tooltipText, /\[untrusted\]\(command:evil\)/);

    const disabledGroup = groups.find(
      (node) => node.kind === "group" && node.group === "disabled"
    );
    assert.ok(disabledGroup);
    const disabledNode = api.provider.getChildren(disabledGroup)[0];
    const disabledItem = api.provider.getTreeItem(disabledNode);
    const disabledTooltipText = (disabledItem.tooltip as vscode.MarkdownString).value.replaceAll(
      "&nbsp;",
      " "
    );
    assert.match(disabledTooltipText, /global-only/);
    assert.match(disabledTooltipText, /状态：已禁用/);
    assert.match(disabledTooltipText, /来源：\/disabled\/global-only/);

    const projectGroup = groups.find((node) => node.kind === "group" && node.group === "project");
    assert.ok(projectGroup);
    const projectNode = api.provider
      .getChildren(projectGroup)
      .find((node) => node.kind === "project" && node.skill.name === "project-only");
    assert.ok(projectNode);
    const projectItem = api.provider.getTreeItem(projectNode);
    assert.equal(projectItem.command, undefined);
    assert.equal((projectItem.iconPath as vscode.ThemeIcon).color?.id, "charts.blue");
    const projectTooltip = projectItem.tooltip as vscode.MarkdownString;
    const projectTooltipText = projectTooltip.value.replaceAll("&nbsp;", " ");
    assert.match(projectTooltipText, /project-only/);
    assert.match(projectTooltipText, /A project-only skill used by the SkillPanel UI tests/);
    assert.match(projectTooltipText, /状态：项目级，始终启用/);
    assert.equal((projectTooltipText.match(/来源：/g) ?? []).length, 2);

    const conflictGroup = groups.find(
      (node) => node.kind === "group" && node.group === "conflict"
    );
    assert.ok(conflictGroup);
    assert.deepEqual(
      api.provider
        .getChildren(conflictGroup)
        .filter((node) => node.kind === "conflict")
        .map((node) => (node.kind === "conflict" ? node.conflict.name : "")),
      ["canary-alpha", "quarantined"]
    );

    assert.equal(panelMessage(api.model), "开关成功后，从下一轮对话开始生效");
    const originalCatalog = api.model.catalog;
    assert.ok(originalCatalog);
    try {
      api.model.catalog = {
        ...originalCatalog,
        pids: { ...originalCatalog.pids, controller: null }
      };
      assert.equal(panelMessage(api.model), "开关成功后，从下一轮对话开始生效");
    } finally {
      api.model.catalog = originalCatalog;
    }
  });

  test("shows pending without moving the skill or leaving a clickable command", () => {
    const originalPending = api.model.pendingName;
    api.model.pendingName = "canary-alpha";
    try {
      const enabledGroup = api.provider
        .getChildren()
        .find((node) => node.kind === "group" && node.group === "enabled");
      assert.ok(enabledGroup);
      const node = api.provider.getChildren(enabledGroup)[0];
      const item = api.provider.getTreeItem(node);
      assert.equal(node.kind, "global");
      assert.equal((item.iconPath as vscode.ThemeIcon).id, "loading~spin");
      assert.equal(item.description, "切换中…");
      assert.equal(item.command, undefined);
      assert.equal(node.kind === "global" ? node.skill.enabled : false, true);
    } finally {
      api.model.pendingName = originalPending;
    }
  });

  test("provides explicit empty and controller-error refresh rows", () => {
    const originalCatalog = api.model.catalog;
    const originalError = api.model.connectionError;
    assert.ok(originalCatalog);
    try {
      api.model.catalog = { ...originalCatalog, skills: [] };
      const empty = api.provider.getTreeItem(api.provider.getChildren()[0]);
      assert.equal(empty.id, "status:empty");
      assert.equal(empty.command?.command, "skillPanel.refresh");
      const emptyTooltip = empty.tooltip as vscode.MarkdownString;
      assert.match(emptyTooltip.value, /~\/\.agents\/skills/);
      assert.match(emptyTooltip.value, /~\/\.agents\/skills-disabled/);

      api.model.catalog = undefined;
      api.model.connectionError = "offline";
      const error = api.provider.getTreeItem(api.provider.getChildren()[0]);
      assert.equal(error.id, "status:error");
      assert.equal(error.command?.command, "skillPanel.refresh");

      api.model.catalog = originalCatalog;
      const staleRoots = api.provider.getChildren();
      assert.equal(api.provider.getTreeItem(staleRoots[0]).id, "status:error");
      const staleEnabled = staleRoots.find(
        (node) => node.kind === "group" && node.group === "enabled"
      );
      assert.ok(staleEnabled);
      const staleGlobal = api.provider.getTreeItem(api.provider.getChildren(staleEnabled)[0]);
      assert.equal(staleGlobal.command, undefined);
      assert.equal(panelMessage(api.model), "开关成功后，从下一轮对话开始生效");
    } finally {
      api.model.catalog = originalCatalog;
      api.model.connectionError = originalError;
    }
  });

  test("merges duplicate project skills and ignores malformed skills", () => {
    const projectOnly = api.model.projectSkills.find((skill) => skill.name === "project-only");
    assert.equal(projectOnly?.sourcePaths.length, 2);
    assert.ok(api.model.projectSkills.some((skill) => skill.name === "claude-only"));
    assert.ok(!api.model.projectSkills.some((skill) => skill.name === "invalid-skill"));
  });

  test("ignores a skill directory symlink", async () => {
    const folder = vscode.workspace.workspaceFolders?.[0];
    assert.ok(folder);
    const temporary = await fs.mkdtemp(path.join(os.tmpdir(), "skillpanel-symlink-"));
    const target = path.join(temporary, "symlinked");
    const gitRoot = path.dirname(folder.uri.fsPath);
    const link = path.join(gitRoot, ".opencode", "skills", "symlinked");
    await fs.mkdir(target, { recursive: true });
    await fs.writeFile(
      path.join(target, "SKILL.md"),
      "---\nname: symlinked\ndescription: Must be ignored\n---\n"
    );
    try {
      await fs.symlink(target, link, "dir");
      const skills = await new ProjectSkillScanner().scan();
      assert.ok(!skills.some((skill) => skill.name === "symlinked"));
    } finally {
      await fs.rm(link, { force: true });
      await fs.rm(temporary, { recursive: true, force: true });
    }
  });

  test("does not scan hidden skills or cross the Git root", async () => {
    const folder = vscode.workspace.workspaceFolders?.[0];
    assert.ok(folder);
    const gitRoot = path.dirname(folder.uri.fsPath);
    const outsideRoot = path.dirname(gitRoot);
    const hidden = path.join(gitRoot, ".opencode", "skills", ".hidden-decoy");
    const outside = path.join(outsideRoot, ".opencode", "skills", "outside-decoy");
    const document = (name: string) => `---\nname: ${name}\ndescription: Must be ignored\n---\n`;
    try {
      await fs.mkdir(hidden, { recursive: true });
      await fs.writeFile(path.join(hidden, "SKILL.md"), document("hidden-decoy"));
      await fs.mkdir(outside, { recursive: true });
      await fs.writeFile(path.join(outside, "SKILL.md"), document("outside-decoy"));

      const skills = await new ProjectSkillScanner().scan();
      assert.ok(!skills.some((skill) => skill.name === "hidden-decoy"));
      assert.ok(!skills.some((skill) => skill.name === "outside-decoy"));
    } finally {
      await fs.rm(hidden, { recursive: true, force: true });
      await fs.rm(outside, { recursive: true, force: true });
    }
  });

  test("toggles through the command, then GETs authoritative state", async () => {
    const before = api.model.catalog?.revision;
    const previousEnabled = enabled;
    await vscode.commands.executeCommand("skillPanel.toggleSkill", "canary-alpha");
    assert.equal(api.model.catalog?.revision, (before ?? 0) + 1);
    assert.equal(api.model.catalog?.skills.find((skill) => skill.name === "canary-alpha")?.enabled, !previousEnabled);
    assert.equal(api.model.pendingName, undefined);
  });

  test("manual refresh is required for external state changes", async () => {
    const staleRevision = api.model.catalog?.revision;
    enabled = !enabled;
    revision += 1;
    assert.equal(api.model.catalog?.revision, staleRevision);
    await vscode.commands.executeCommand("skillPanel.refresh");
    assert.equal(api.model.catalog?.revision, revision);
    assert.equal(api.model.catalog?.skills.find((skill) => skill.name === "canary-alpha")?.enabled, enabled);
  });
});
