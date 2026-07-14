import { defineConfig } from "@vscode/test-cli";

export default defineConfig({
  files: "out-test/test/extension/**/*.test.js",
  version: "stable",
  workspaceFolder: "test-fixtures/skill-panel.code-workspace",
  launchArgs: [
    "--disable-extensions",
    "--user-data-dir=/tmp/skillpanel-vscode-test-user-data",
    "--extensions-dir=/tmp/skillpanel-vscode-test-extensions"
  ]
});
