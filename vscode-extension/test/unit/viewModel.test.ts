import * as assert from "node:assert/strict";
import { buildConflicts } from "../../src/viewModel";
import type { SkillCatalog } from "../../src/types";

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
  pids: { opencode: 1, hermes: 2, controller: 3 }
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
