# SkillPanel

SkillPanel is a workspace extension for the SkillPanel OpenCode/Hermes container. It shows global shared skills from the controller API and read-only project skills from the current workspace.

## Behavior

- Click a global skill row to enable or disable it.
- Project skills are always enabled and cannot be toggled.
- External filesystem changes appear only after the refresh command.
- A successful switch affects the next OpenCode/Hermes turn. It does not cancel work already in progress.

The extension calls `http://127.0.0.1:8787` from the remote workspace extension host. It never edits skill directories or Agent configuration directly.
