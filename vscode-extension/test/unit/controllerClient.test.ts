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
    pids: { opencode: 10, hermes: 11, controller: 12 }
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
        pids: { opencode: 10, hermes: 11, controller: 12 },
        latency_ms: 12.5
      });
    });
    const result = await client.setSkill("name with space", false, 7);
    assert.deepEqual(JSON.parse(body), { enabled: false, expected_revision: 7 });
    assert.equal(result.revision, 8);
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
