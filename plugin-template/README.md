# StateBreaker 插件模板

这是一个不会发送网络请求的 dry-run executor，用于证明插件可以独立安装和发现。

```powershell
python -m pip install -e .\plugin-template
statebreaker plugins list
statebreaker attack .\examples\coupon-race\attack-plan.yaml `
  --workflow .\examples\coupon-race\workflow.yaml `
  --plugin template.dry-run
```

复制本目录后，至少需要修改：

- 包名和 `project.name`；
- entry point 名称；
- `PluginManifest.plugin_id`，组内必须唯一；
- 插件方法实现及能力列表。

不要修改 StateBreaker 核心注册表。插件通过 `pyproject.toml` 的 entry point 自动加入。
