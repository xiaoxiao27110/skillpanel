import * as assert from "node:assert/strict";
import { ControllerError, type SkillControllerClient } from "../../src/controllerClient";
import { SkillPanelModel, type Notifications } from "../../src/skillPanelModel";
import type { ProjectSkillSource } from "../../src/projectScanner";
import type { SkillCatalog, ToggleResult } from "../../src/types";

function makeCatalog(revision: number, enabled: boolean): SkillCatalog {
  return {
    revision,
    skills: [
      { name: "alpha", description: "Alpha", enabled, location: `/skills/${String(enabled)}/alpha` }
    ],
    reconciliations: [],
    pids: { opencode: 1, hermes: 2, controller: 3 }
  };
}

function toggleResult(revision: number, enabled: boolean): ToggleResult {
  return {
    ok: true,
    changed: true,
    name: "alpha",
    enabled,
    revision,
    reconciliations: [],
    opencode_skills: enabled ? ["alpha"] : [],
    pids: { opencode: 1, hermes: 2, controller: 3 },
    latency_ms: 1,
    hermes_refresh: "next-turn"
  };
}

class FakeProjectSource implements ProjectSkillSource {
  scans = 0;
  async scan() {
    this.scans += 1;
    return [];
  }
}

class NotificationLog implements Notifications {
  warnings: string[] = [];
  errors: string[] = [];
  warning(message: string) {
    this.warnings.push(message);
  }
  error(message: string) {
    this.errors.push(message);
  }
}

suite("SkillPanelModel", () => {
  test("does not optimistically move state and serializes toggles", async () => {
    let release!: () => void;
    const blocked = new Promise<void>((resolve) => {
      release = resolve;
    });
    let catalog = makeCatalog(1, true);
    let puts = 0;
    const controller: SkillControllerClient = {
      getSkills: async () => catalog,
      setSkill: async (_name, enabled, expectedRevision) => {
        puts += 1;
        assert.equal(expectedRevision, 1);
        await blocked;
        catalog = makeCatalog(2, enabled);
        return toggleResult(2, enabled);
      }
    };
    const model = new SkillPanelModel(controller, new FakeProjectSource(), new NotificationLog());
    await model.refresh();

    const first = model.toggle("alpha");
    const second = model.toggle("alpha");
    assert.equal(model.pendingName, "alpha");
    assert.equal(model.catalog?.skills[0].enabled, true);
    assert.equal(puts, 1);
    release();
    await Promise.all([first, second]);

    assert.equal(model.catalog?.revision, 2);
    assert.equal(model.catalog?.skills[0].enabled, false);
    assert.equal(model.pendingName, undefined);
    assert.equal(puts, 1);
  });

  test("refreshes without retrying after 409", async () => {
    let gets = 0;
    let puts = 0;
    const notifications = new NotificationLog();
    const controller: SkillControllerClient = {
      getSkills: async () => {
        gets += 1;
        return gets === 1 ? makeCatalog(5, true) : makeCatalog(6, false);
      },
      setSkill: async () => {
        puts += 1;
        throw new ControllerError("conflict", 409, undefined, 6);
      }
    };
    const model = new SkillPanelModel(controller, new FakeProjectSource(), notifications);
    await model.refresh();
    await model.toggle("alpha");

    assert.equal(puts, 1);
    assert.equal(gets, 2);
    assert.equal(model.catalog?.revision, 6);
    assert.match(notifications.warnings[0], /revision 6/);
  });

  for (const status of [503, 507]) {
    test(`shows rollback detail and refreshes after ${status}`, async () => {
      let gets = 0;
      const notifications = new NotificationLog();
      const controller: SkillControllerClient = {
        getSkills: async () => {
          gets += 1;
          return makeCatalog(4, true);
        },
        setSkill: async () => {
          throw new ControllerError("rollback was complete", status);
        }
      };
      const model = new SkillPanelModel(controller, new FakeProjectSource(), notifications);
      await model.refresh();
      await model.toggle("alpha");

      assert.equal(gets, 2);
      assert.match(notifications.errors[0], /回滚/);
      assert.match(notifications.errors[0], /rollback was complete/);
      assert.equal(model.catalog?.skills[0].enabled, true);
    });
  }

  test("GETs authoritative state after an idempotent PUT result", async () => {
    let gets = 0;
    const controller: SkillControllerClient = {
      getSkills: async () => {
        gets += 1;
        return makeCatalog(7, true);
      },
      setSkill: async () => ({ ...toggleResult(7, false), changed: false })
    };
    const model = new SkillPanelModel(controller, new FakeProjectSource(), new NotificationLog());
    await model.refresh();
    await model.toggle("alpha");

    assert.equal(gets, 2);
    assert.equal(model.catalog?.revision, 7);
    assert.equal(model.catalog?.skills[0].enabled, true);
  });

  test("retains the last catalog when refresh fails", async () => {
    let shouldFail = false;
    const notifications = new NotificationLog();
    const controller: SkillControllerClient = {
      getSkills: async () => {
        if (shouldFail) {
          throw new ControllerError("offline");
        }
        return makeCatalog(3, true);
      },
      setSkill: async () => toggleResult(4, false)
    };
    const model = new SkillPanelModel(controller, new FakeProjectSource(), notifications);
    await model.refresh();
    shouldFail = true;
    await model.refresh();
    assert.equal(model.catalog?.revision, 3);
    assert.match(model.connectionError ?? "", /offline/);
    assert.match(notifications.errors[0], /无法刷新/);
  });

  test("does not toggle from a stale catalog after refresh fails", async () => {
    let shouldFail = false;
    let puts = 0;
    const controller: SkillControllerClient = {
      getSkills: async () => {
        if (shouldFail) {
          throw new ControllerError("offline");
        }
        return makeCatalog(3, true);
      },
      setSkill: async () => {
        puts += 1;
        return toggleResult(4, false);
      }
    };
    const model = new SkillPanelModel(controller, new FakeProjectSource(), new NotificationLog());
    await model.refresh();
    shouldFail = true;
    await model.refresh();
    await model.toggle("alpha");

    assert.equal(puts, 0);
    assert.match(model.connectionError ?? "", /offline/);
  });
});
