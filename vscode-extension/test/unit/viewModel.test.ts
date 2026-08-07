import * as assert from "node:assert/strict";
import { buildConflicts, sceneItemDescription, sceneTooltipLines } from "../../src/viewModel";
import type { Scene, SkillCatalog } from "../../src/types";

const catalog: SkillCatalog = {
  revision: 2,
  skills: [
    { name: "alpha", description: "Alpha", enabled: true, location: "/enabled/alpha" },
    { name: "beta", description: "Beta", enabled: false, location: "/disabled/beta" }
  ],
  reconciliations: [
    {
      name: "gamma",
      policy: "disabled-wins",
      action: "quarantined-enabled-copy",
      disabled_location: "/disabled/gamma",
      quarantine_location: "/disabled/.skillpanel-conflicts/gamma/1"
    }
  ],
  pids: { opencode: 1, hermes: 2, codex: 4, controller: 3 }
};

suite("buildConflicts", () => {
  test("combines cross-scope and reconciliation conflicts", () => {
    const result = buildConflicts(catalog, [
      { name: "alpha", description: "Project alpha", sourcePaths: ["/project/alpha"] },
      { name: "beta", description: "Project beta", sourcePaths: ["/project/beta"] },
      { name: "project-only", description: "Only", sourcePaths: ["/project/only"] }
    ]);
    assert.equal(result.length, 3);
    assert.deepEqual(
      result.map((item) => item.name),
      ["alpha", "beta", "gamma"]
    );
    assert.match(result[0].summary, /同名/);
    assert.match(result[1].summary, /仍然启用/);
    assert.match(result[2].summary, /已隔离/);
  });

  test("returns no conflicts before a catalog is loaded", () => {
    assert.deepEqual(buildConflicts(undefined, []), []);
  });
});

suite("scene view model", () => {
  const active: Scene = { name: "默认", disabled: [], active: true };
  const inactive: Scene = { name: "写作", disabled: ["pdf", "docx"], active: false };
  const empty: Scene = { name: "全量", disabled: [], active: false };

  test("marks the active scene with a 当前 description", () => {
    assert.equal(sceneItemDescription(active), "当前");
  });

  test("shows the disabled count for inactive scenes", () => {
    assert.equal(sceneItemDescription(inactive), "关闭 2 项");
  });

  test("omits the description when an inactive scene disables nothing", () => {
    assert.equal(sceneItemDescription(empty), undefined);
  });

  test("lists the disabled names in the tooltip", () => {
    const lines = sceneTooltipLines(inactive);
    assert.equal(lines[0], "写作");
    assert.ok(lines.some((line) => line.includes("pdf")));
    assert.ok(lines.some((line) => line.includes("docx")));
  });

  test("notes an empty disabled list in the tooltip", () => {
    assert.ok(sceneTooltipLines(active).some((line) => line.includes("全部启用")));
  });
});
