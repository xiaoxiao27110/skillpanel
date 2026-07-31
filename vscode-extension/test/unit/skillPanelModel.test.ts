import * as assert from "node:assert/strict";
import { ControllerError, type SkillControllerClient } from "../../src/controllerClient";
import { SkillPanelModel, type Notifications } from "../../src/skillPanelModel";
import type { ProjectSkillSource } from "../../src/projectScanner";
import type {
  SceneCatalog,
  SceneMutationResult,
  SkillCatalog,
  ToggleResult
} from "../../src/types";

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
    hermes_refresh: "next-turn",
    active_scene: "默认",
    scenes_revision: 1
  };
}

function makeScenes(revision: number): SceneCatalog {
  return {
    revision,
    active: "默认",
    scenes: [
      { name: "默认", disabled: [], active: true },
      { name: "写作", disabled: ["alpha"], active: false }
    ],
    pids: { opencode: 1, hermes: 2, controller: 3 }
  };
}

function sceneMutation(revision: number): SceneMutationResult {
  return { ...makeScenes(revision), ok: true, skill_revision: revision, latency_ms: 1 };
}

function sceneClientDefaults(): SkillControllerClient {
  return {
    getSkills: async () => makeCatalog(1, true),
    setSkill: async (_name, enabled, expectedRevision) => toggleResult(expectedRevision, enabled),
    getScenes: async () => makeScenes(1),
    createScene: async () => sceneMutation(2),
    activateScene: async () => ({ ...sceneMutation(2), changed: true }),
    renameScene: async () => sceneMutation(2),
    deleteScene: async () => ({ ...makeScenes(2), ok: true })
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
      ...sceneClientDefaults(),
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
      ...sceneClientDefaults(),
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
        ...sceneClientDefaults(),
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
      ...sceneClientDefaults(),
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
      ...sceneClientDefaults(),
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
      ...sceneClientDefaults(),
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

suite("SkillPanelModel scenes", () => {
  test("activates a scene with the scenes revision, not the skill revision", async () => {
    let sceneGets = 0;
    let activates = 0;
    const controller: SkillControllerClient = {
      ...sceneClientDefaults(),
      getSkills: async () => makeCatalog(9, true),
      getScenes: async () => {
        sceneGets += 1;
        return makeScenes(sceneGets === 1 ? 3 : 4);
      },
      activateScene: async (name, expectedRevision) => {
        activates += 1;
        assert.equal(name, "写作");
        assert.equal(expectedRevision, 3);
        return { ...sceneMutation(4), changed: true };
      }
    };
    const model = new SkillPanelModel(controller, new FakeProjectSource(), new NotificationLog());
    await model.refresh();
    assert.equal(model.catalog?.revision, 9);
    assert.equal(model.scenes?.revision, 3);

    await model.activateScene("写作");

    assert.equal(activates, 1);
    assert.equal(sceneGets, 2);
    assert.equal(model.scenes?.revision, 4);
    assert.equal(model.pendingSceneName, undefined);
  });

  test("refreshes without retrying after a scene 409", async () => {
    let sceneGets = 0;
    let activates = 0;
    const notifications = new NotificationLog();
    const controller: SkillControllerClient = {
      ...sceneClientDefaults(),
      getSkills: async () => makeCatalog(5, true),
      getScenes: async () => {
        sceneGets += 1;
        return makeScenes(sceneGets === 1 ? 3 : 4);
      },
      activateScene: async () => {
        activates += 1;
        throw new ControllerError("conflict", 409, undefined, 4);
      }
    };
    const model = new SkillPanelModel(controller, new FakeProjectSource(), notifications);
    await model.refresh();
    await model.activateScene("写作");

    assert.equal(activates, 1);
    assert.equal(sceneGets, 2);
    assert.equal(model.scenes?.revision, 4);
    assert.match(notifications.warnings[0], /revision 4/);
    assert.equal(notifications.errors.length, 0);
  });

  test("creates a scene without a revision and re-reads state", async () => {
    let creates = 0;
    let sceneGets = 0;
    const controller: SkillControllerClient = {
      ...sceneClientDefaults(),
      getSkills: async () => makeCatalog(5, true),
      getScenes: async () => {
        sceneGets += 1;
        return sceneGets === 1
          ? makeScenes(3)
          : {
              ...makeScenes(4),
              active: "阅读",
              scenes: [
                ...makeScenes(4).scenes.map((scene) => ({ ...scene, active: false })),
                { name: "阅读", disabled: [], active: true }
              ]
            };
      },
      createScene: async (name) => {
        creates += 1;
        assert.equal(name, "阅读");
        return sceneMutation(4);
      }
    };
    const model = new SkillPanelModel(controller, new FakeProjectSource(), new NotificationLog());
    await model.refresh();
    await model.createScene("阅读");

    assert.equal(creates, 1);
    assert.equal(sceneGets, 2);
    assert.equal(model.scenes?.active, "阅读");
    assert.equal(model.pendingSceneName, undefined);
  });

  test("follows the server's automatic switch after deleting the active scene", async () => {
    let deletes = 0;
    let sceneGets = 0;
    const controller: SkillControllerClient = {
      ...sceneClientDefaults(),
      getSkills: async () => makeCatalog(5, true),
      getScenes: async () => {
        sceneGets += 1;
        return sceneGets === 1
          ? { ...makeScenes(3), active: "写作", scenes: [
              { name: "默认", disabled: [], active: false },
              { name: "写作", disabled: ["alpha"], active: true }
            ] }
          : { revision: 4, active: "默认", scenes: [{ name: "默认", disabled: [], active: true }], pids: makeScenes(4).pids };
      },
      deleteScene: async (name, expectedRevision) => {
        deletes += 1;
        assert.equal(name, "写作");
        assert.equal(expectedRevision, 3);
        return { ...makeScenes(4), ok: true };
      }
    };
    const model = new SkillPanelModel(controller, new FakeProjectSource(), new NotificationLog());
    await model.refresh();
    assert.equal(model.scenes?.active, "写作");
    await model.deleteScene("写作");

    assert.equal(deletes, 1);
    assert.equal(model.scenes?.active, "默认");
    assert.deepEqual(
      model.scenes?.scenes.map((scene) => scene.name),
      ["默认"]
    );
  });

  test("re-reads scenes after a toggle so disabled counts stay current", async () => {
    let sceneGets = 0;
    const controller: SkillControllerClient = {
      ...sceneClientDefaults(),
      getSkills: async () => makeCatalog(5, true),
      getScenes: async () => {
        sceneGets += 1;
        return makeScenes(3);
      },
      setSkill: async () => toggleResult(6, false)
    };
    const model = new SkillPanelModel(controller, new FakeProjectSource(), new NotificationLog());
    await model.refresh();
    await model.toggle("alpha");

    assert.equal(sceneGets, 2);
    assert.equal(model.scenes?.revision, 3);
  });

  test("blocks scene mutations while a skill toggle is pending", async () => {
    let activates = 0;
    let release!: () => void;
    const blocked = new Promise<void>((resolve) => {
      release = resolve;
    });
    let catalog = makeCatalog(1, true);
    const controller: SkillControllerClient = {
      ...sceneClientDefaults(),
      getSkills: async () => catalog,
      setSkill: async (_name, enabled) => {
        await blocked;
        catalog = makeCatalog(2, enabled);
        return toggleResult(2, enabled);
      },
      activateScene: async () => {
        activates += 1;
        return { ...sceneMutation(2), changed: true };
      }
    };
    const model = new SkillPanelModel(controller, new FakeProjectSource(), new NotificationLog());
    await model.refresh();

    const toggling = model.toggle("alpha");
    await model.activateScene("写作");
    assert.equal(activates, 0);
    release();
    await toggling;
  });
});
