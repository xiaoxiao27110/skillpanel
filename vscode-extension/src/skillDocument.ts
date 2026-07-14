import { parse } from "yaml";

export const SKILL_NAME_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export interface SkillMetadata {
  name: string;
  description: string;
}

export function parseSkillDocument(text: string, directoryName: string): SkillMetadata | undefined {
  const match = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(text);
  if (!match) {
    return undefined;
  }

  let frontmatter: unknown;
  try {
    frontmatter = parse(match[1]);
  } catch {
    return undefined;
  }
  if (!isRecord(frontmatter)) {
    return undefined;
  }

  const { name, description } = frontmatter;
  if (
    typeof name !== "string" ||
    !SKILL_NAME_PATTERN.test(name) ||
    name !== directoryName ||
    typeof description !== "string" ||
    description.length < 1 ||
    description.length > 1024
  ) {
    return undefined;
  }
  return { name, description };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
