import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

interface ExtensionManifest {
  activationEvents: string[];
  extensionKind: string[];
  contributes: {
    commands: Array<{ command: string }>;
    menus: Record<string, Array<{ command: string; when?: string; group?: string }>>;
    views: Record<string, Array<{ id: string }>>;
    viewsContainers: Record<string, Array<{ id: string }>>;
  };
}

suite("extension contribution boundary", () => {
  const extensionRoot = process.cwd();
  const manifest = JSON.parse(
    fs.readFileSync(path.join(extensionRoot, "package.json"), "utf8")
  ) as ExtensionManifest;

  test("contributes only the agreed view and refresh command", () => {
    assert.deepEqual(Object.keys(manifest.contributes), [
      "commands",
      "viewsContainers",
      "views",
      "menus"
    ]);
    assert.deepEqual(manifest.extensionKind, ["workspace"]);
    assert.deepEqual(manifest.activationEvents, ["onView:skillPanel.skills"]);
    assert.deepEqual(
      manifest.contributes.commands.map(({ command }) => command),
      ["skillPanel.refresh"]
    );
    assert.deepEqual(Object.keys(manifest.contributes.viewsContainers), ["activitybar"]);
    assert.deepEqual(
      manifest.contributes.viewsContainers.activitybar.map(({ id }) => id),
      ["skillPanel"]
    );
    assert.deepEqual(Object.keys(manifest.contributes.views), ["skillPanel"]);
    assert.deepEqual(
      manifest.contributes.views.skillPanel.map(({ id }) => id),
      ["skillPanel.skills"]
    );
    assert.equal(manifest.contributes.menus.commandPalette, undefined);
    assert.deepEqual(manifest.contributes.menus["view/title"], [
      {
        command: "skillPanel.refresh",
        when: "view == skillPanel.skills",
        group: "navigation"
      }
    ]);
  });

  test("does not register automatic filesystem watchers", () => {
    const source = typescriptFiles(path.join(extensionRoot, "src"))
      .map((file) => fs.readFileSync(file, "utf8"))
      .join("\n");
    assert.doesNotMatch(source, /createFileSystemWatcher|\bwatchFile\s*\(|\bfs\.watch\s*\(/);
  });
});

function typescriptFiles(root: string): string[] {
  return fs.readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(root, entry.name);
    if (entry.isDirectory()) {
      return typescriptFiles(target);
    }
    return entry.isFile() && entry.name.endsWith(".ts") ? [target] : [];
  });
}
