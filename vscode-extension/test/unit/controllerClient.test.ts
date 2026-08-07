import * as assert from "node:assert/strict";
import { ControllerClient, ControllerError } from "../../src/controllerClient";

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}

function catalog() {
  return {
    revision: 7,
    skills: [
      {
        name: "pdf",
        description: "Create PDFs",
        enabled: true,
        location: "/skills/pdf"
      }
    ],
    reconciliations: [],
    pids: { opencode: 10, hermes: 11, codex: 13, controller: 12 }
  };
}

suite("ControllerClient", () => {
  test("parses GET /skills", async () => {
    const client = new ControllerClient("http://controller", 100, async (input, init) => {
      assert.equal(String(input), "http://controller/skills");
      assert.equal(init?.method, "GET");
      return response(catalog());
    });
    assert.deepEqual(await client.getSkills(), catalog());
  });

  test("sends expected_revision and URL-encodes the skill", async () => {
    let body = "";
    const client = new ControllerClient("http://controller", 100, async (input, init) => {
      assert.equal(String(input), "http://controller/skills/name%20with%20space");
      body = String(init?.body);
      return response({
        ok: true,
        changed: true,
        name: "name with space",
        enabled: false,
        revision: 8,
        reconciliations: [],
        hermes_refresh: "next-turn",
        opencode_skills: [],
        pids: { opencode: 10, hermes: 11, codex: 13, controller: 12 },
        latency_ms: 12.5,
        active_scene: "默认",
        scenes_revision: 3
      });
    });
    const result = await client.setSkill("name with space", false, 7);
    assert.deepEqual(JSON.parse(body), { enabled: false, expected_revision: 7 });
    assert.equal(result.revision, 8);
    assert.equal(result.active_scene, "默认");
    assert.equal(result.scenes_revision, 3);
  });

  test("preserves 409 current_revision", async () => {
    const client = new ControllerClient("http://controller", 100, async () =>
      response({ detail: { message: "Revision conflict", current_revision: 9 } }, 409)
    );
    await assert.rejects(
      client.setSkill("pdf", false, 7),
      (error: unknown) =>
        error instanceof ControllerError && error.status === 409 && error.currentRevision === 9
    );
  });

  for (const status of [503, 507]) {
    test(`returns rollback detail for HTTP ${status}`, async () => {
      const client = new ControllerClient("http://controller", 100, async () =>
        response({ detail: "refresh failed; rollback was complete" }, status)
      );
      await assert.rejects(
        client.setSkill("pdf", false, 7),
        (error: unknown) =>
          error instanceof ControllerError &&
          error.status === status &&
          error.message.includes("rollback was complete")
      );
    });
  }

  for (const status of [400, 404]) {
    test(`preserves HTTP ${status}`, async () => {
      const client = new ControllerClient("http://controller", 100, async () =>
        response({ detail: "request rejected" }, status)
      );
      await assert.rejects(
        client.setSkill("pdf", false, 7),
        (error: unknown) => error instanceof ControllerError && error.status === status
      );
    });
  }

  test("rejects malformed success data", async () => {
    const client = new ControllerClient("http://controller", 100, async () =>
      response({ ...catalog(), revision: "7" })
    );
    await assert.rejects(client.getSkills(), /invalid catalog\.revision/);
  });

  test("rejects invalid JSON", async () => {
    const client = new ControllerClient(
      "http://controller",
      100,
      async () => new Response("not-json", { status: 200 })
    );
    await assert.rejects(client.getSkills(), /invalid JSON/);
  });

  test("aborts requests after the configured timeout", async () => {
    const client = new ControllerClient(
      "http://controller",
      1,
      async (_input, init) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new Error("aborted")));
        })
    );
    await assert.rejects(client.getSkills(), /request failed: aborted/);
  });

  test("keeps the timeout active while reading the response body", async () => {
    const client = new ControllerClient("http://controller", 5, async (_input, init) => {
      const body = new ReadableStream<Uint8Array>({
        start(stream) {
          init?.signal?.addEventListener("abort", () => stream.error(new Error("aborted body")));
        }
      });
      return new Response(body, { status: 200 });
    });
    await assert.rejects(client.getSkills(), /request failed: aborted body/);
  });
});

function sceneCatalog() {
  return {
    revision: 3,
    active: "默认",
    scenes: [
      { name: "默认", disabled: [], active: true },
      { name: "写作", disabled: ["pdf", "docx"], active: false }
    ],
    pids: { opencode: 10, hermes: 11, codex: 13, controller: 12 }
  };
}

function sceneMutation() {
  return { ...sceneCatalog(), ok: true, skill_revision: 8, latency_ms: 4.5 };
}

suite("ControllerClient scenes", () => {
  test("parses GET /scenes", async () => {
    const client = new ControllerClient("http://controller", 100, async (input, init) => {
      assert.equal(String(input), "http://controller/scenes");
      assert.equal(init?.method, "GET");
      return response(sceneCatalog());
    });
    assert.deepEqual(await client.getScenes(), sceneCatalog());
  });

  test("creates a scene with POST /scenes", async () => {
    let body = "";
    const client = new ControllerClient("http://controller", 100, async (input, init) => {
      assert.equal(String(input), "http://controller/scenes");
      assert.equal(init?.method, "POST");
      body = String(init?.body);
      return response(sceneMutation());
    });
    const result = await client.createScene("写作");
    assert.deepEqual(JSON.parse(body), { name: "写作" });
    assert.equal(result.ok, true);
    assert.equal(result.skill_revision, 8);
    assert.equal(result.latency_ms, 4.5);
    assert.equal(result.revision, 3);
  });

  test("activates a scene with URL-encoded name and scenes revision", async () => {
    let body = "";
    const client = new ControllerClient("http://controller", 100, async (input, init) => {
      assert.equal(String(input), `http://controller/scenes/${encodeURIComponent("写作")}/activate`);
      assert.equal(init?.method, "PUT");
      body = String(init?.body);
      return response({ ...sceneMutation(), changed: true });
    });
    const result = await client.activateScene("写作", 3);
    assert.deepEqual(JSON.parse(body), { expected_revision: 3 });
    assert.equal(result.changed, true);
    assert.equal(result.active, "默认");
  });

  test("renames a scene with new_name and expected_revision", async () => {
    let body = "";
    const client = new ControllerClient("http://controller", 100, async (input, init) => {
      assert.equal(String(input), `http://controller/scenes/${encodeURIComponent("写作")}`);
      assert.equal(init?.method, "PUT");
      body = String(init?.body);
      return response(sceneMutation());
    });
    const result = await client.renameScene("写作", "阅读", 3);
    assert.deepEqual(JSON.parse(body), { new_name: "阅读", expected_revision: 3 });
    assert.equal(result.ok, true);
  });

  test("deletes a scene with expected_revision in the DELETE body", async () => {
    let body = "";
    const client = new ControllerClient("http://controller", 100, async (input, init) => {
      assert.equal(String(input), `http://controller/scenes/${encodeURIComponent("写作")}`);
      assert.equal(init?.method, "DELETE");
      body = String(init?.body);
      return response({ ...sceneCatalog(), ok: true });
    });
    const result = await client.deleteScene("写作", 3);
    assert.deepEqual(JSON.parse(body), { expected_revision: 3 });
    assert.equal(result.ok, true);
    assert.deepEqual(
      result.scenes.map((scene) => scene.name),
      ["默认", "写作"]
    );
  });

  test("preserves 409 current_revision on activate", async () => {
    const client = new ControllerClient("http://controller", 100, async () =>
      response({ detail: { message: "Revision conflict", current_revision: 4 } }, 409)
    );
    await assert.rejects(
      client.activateScene("写作", 3),
      (error: unknown) =>
        error instanceof ControllerError && error.status === 409 && error.currentRevision === 4
    );
  });

  test("preserves 409 on create without a current_revision", async () => {
    const client = new ControllerClient("http://controller", 100, async () =>
      response({ detail: "scene already exists" }, 409)
    );
    await assert.rejects(
      client.createScene("写作"),
      (error: unknown) =>
        error instanceof ControllerError &&
        error.status === 409 &&
        error.currentRevision === undefined
    );
  });

  test("rejects malformed scene catalogs", async () => {
    const client = new ControllerClient("http://controller", 100, async () =>
      response({ ...sceneCatalog(), scenes: [{ name: "默认", disabled: "pdf", active: true }] })
    );
    await assert.rejects(client.getScenes(), /invalid scene\.disabled/);
  });

  test("rejects an activate result without changed", async () => {
    const client = new ControllerClient("http://controller", 100, async () =>
      response(sceneMutation())
    );
    await assert.rejects(client.activateScene("写作", 3), /invalid scenes\.changed/);
  });
});
