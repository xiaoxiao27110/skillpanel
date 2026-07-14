import * as assert from "node:assert/strict";
import { parseSkillDocument } from "../../src/skillDocument";

suite("parseSkillDocument", () => {
  test("parses valid front matter", () => {
    assert.deepEqual(
      parseSkillDocument("---\nname: pdf\ndescription: Create PDFs\n---\n# PDF\n", "pdf"),
      { name: "pdf", description: "Create PDFs" }
    );
  });

  test("supports YAML descriptions", () => {
    assert.deepEqual(
      parseSkillDocument(
        "---\nname: multi-line\ndescription: >-\n  First line\n  second line\n---\n",
        "multi-line"
      ),
      { name: "multi-line", description: "First line second line" }
    );
  });

  test("ignores missing or invalid front matter", () => {
    assert.equal(parseSkillDocument("# no front matter", "broken"), undefined);
    assert.equal(parseSkillDocument("---\nname: Broken\ndescription: no\n---\n", "Broken"), undefined);
    assert.equal(
      parseSkillDocument("---\nname: other\ndescription: mismatch\n---\n", "expected"),
      undefined
    );
    assert.equal(parseSkillDocument("---\nname: empty\ndescription: ''\n---\n", "empty"), undefined);
  });
});
