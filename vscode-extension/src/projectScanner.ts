import * as path from "node:path";
import * as vscode from "vscode";
import { parseSkillDocument } from "./skillDocument";
import type { ProjectSkill } from "./types";

const PROJECT_SKILL_PATHS = [
  [".opencode", "skills"],
  [".agents", "skills"],
  [".claude", "skills"]
] as const;

export interface ProjectSkillSource {
  scan(): Promise<ProjectSkill[]>;
}

export class ProjectSkillScanner implements ProjectSkillSource {
  constructor(private readonly fileSystem: vscode.FileSystem = vscode.workspace.fs) {}

  async scan(): Promise<ProjectSkill[]> {
    const merged = new Map<string, ProjectSkill>();
    for (const folder of vscode.workspace.workspaceFolders ?? []) {
      const gitRoot = await this.findGitRoot(folder.uri);
      for (const directory of directoriesThrough(folder.uri, gitRoot)) {
        for (const segments of PROJECT_SKILL_PATHS) {
          await this.scanRoot(vscode.Uri.joinPath(directory, ...segments), merged);
        }
      }
    }
    return [...merged.values()]
      .map((skill) => ({ ...skill, sourcePaths: [...skill.sourcePaths].sort() }))
      .sort((left, right) => left.name.localeCompare(right.name));
  }

  private async findGitRoot(start: vscode.Uri): Promise<vscode.Uri> {
    let current = start;
    for (;;) {
      if (await this.exists(vscode.Uri.joinPath(current, ".git"))) {
        return current;
      }
      const parent = parentUri(current);
      if (parent.path === current.path) {
        return start;
      }
      current = parent;
    }
  }

  private async scanRoot(root: vscode.Uri, merged: Map<string, ProjectSkill>): Promise<void> {
    let entries: [string, vscode.FileType][];
    try {
      entries = await this.fileSystem.readDirectory(root);
    } catch {
      return;
    }

    for (const [directoryName, fileType] of entries) {
      if (
        directoryName.startsWith(".") ||
        (fileType & vscode.FileType.SymbolicLink) !== 0 ||
        (fileType & vscode.FileType.Directory) === 0
      ) {
        continue;
      }
      const skillFile = vscode.Uri.joinPath(root, directoryName, "SKILL.md");
      let content: Uint8Array;
      try {
        content = await this.fileSystem.readFile(skillFile);
      } catch {
        continue;
      }
      const metadata = parseSkillDocument(new TextDecoder().decode(content), directoryName);
      if (!metadata) {
        continue;
      }

      const sourcePath = skillFile.fsPath;
      const existing = merged.get(metadata.name);
      if (existing) {
        if (!existing.sourcePaths.includes(sourcePath)) {
          existing.sourcePaths.push(sourcePath);
        }
      } else {
        merged.set(metadata.name, {
          ...metadata,
          sourcePaths: [sourcePath]
        });
      }
    }
  }

  private async exists(uri: vscode.Uri): Promise<boolean> {
    try {
      await this.fileSystem.stat(uri);
      return true;
    } catch {
      return false;
    }
  }
}

function parentUri(uri: vscode.Uri): vscode.Uri {
  return uri.with({ path: path.posix.dirname(uri.path) });
}

function directoriesThrough(start: vscode.Uri, end: vscode.Uri): vscode.Uri[] {
  const result: vscode.Uri[] = [];
  let current = start;
  for (;;) {
    result.push(current);
    if (current.path === end.path) {
      return result;
    }
    const parent = parentUri(current);
    if (parent.path === current.path) {
      return result;
    }
    current = parent;
  }
}

export const testing = { directoriesThrough, parentUri, PROJECT_SKILL_PATHS };
