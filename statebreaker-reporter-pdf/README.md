# statebreaker-reporter-pdf

最小 `statebreaker.reporter` 插件，`plugin_id = team.pdf-reporter`。

把一次完整 `RunBundle`（workflow + attack plan + raw result + findings）渲染为：

- `statebreaker-report.pdf` — 主报告
- `report-summary.json` — 同内容的机器可读摘要

PDF 使用内置 Latin 字体，避免依赖系统中文字体；字段名与状态以 ASCII/JSON 展示。

## 安装

```powershell
python -m pip install -e .\statebreaker-reporter-pdf
statebreaker plugins list --group statebreaker.reporter
```

## 使用

先准备 `run-bundle.json`（含 findings），再：

```powershell
statebreaker report .\run-bundle.json `
  --plugin team.pdf-reporter `
  --output-dir .\report
```

输出目录中的 `statebreaker-report.pdf` 即为报告文件。
