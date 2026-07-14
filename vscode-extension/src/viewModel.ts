import type { ConflictItem, ProjectSkill, SkillCatalog } from "./types";

export function buildConflicts(
  catalog: SkillCatalog | undefined,
  projectSkills: readonly ProjectSkill[]
): ConflictItem[] {
  if (!catalog) {
    return [];
  }

  const globalByName = new Map(catalog.skills.map((skill) => [skill.name, skill]));
  const conflicts: ConflictItem[] = [];
  for (const project of projectSkills) {
    const global = globalByName.get(project.name);
    if (!global) {
      continue;
    }
    conflicts.push({
      id: `scope:${project.name}`,
      name: project.name,
      summary: global.enabled
        ? "全局与项目 Skill 同名，OpenCode 要求名称唯一"
        : "全局版本已禁用，当前项目的同名 Skill 仍然启用",
      sourcePaths: [global.location, ...project.sourcePaths]
    });
  }

  for (const reconciliation of catalog.reconciliations) {
    const location = reconciliation.quarantine_location ?? reconciliation.disabled_location;
    conflicts.push({
      id: `reconciliation:${reconciliation.name}:${location ?? reconciliation.action}`,
      name: reconciliation.name,
      summary:
        reconciliation.policy === "disabled-wins"
          ? "检测到重复安装；禁用版本优先，新启用副本已隔离"
          : `控制器已执行 ${reconciliation.action}`,
      sourcePaths: [reconciliation.disabled_location, reconciliation.quarantine_location].filter(
        (value): value is string => typeof value === "string"
      )
    });
  }

  return conflicts.sort((left, right) =>
    left.name === right.name ? left.id.localeCompare(right.id) : left.name.localeCompare(right.name)
  );
}
