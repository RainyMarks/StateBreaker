# 协作约定

## 开发环境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
ruff check .
mypy
```

## 分支和提交

- 每个功能使用独立分支，不直接在 `main` 上混合多个模块。
- 插件优先放在独立 Python 包中，通过 entry point 接入。
- 修改公共模型、插件签名或 CLI 前，必须同步更新契约文档与测试。
- 不提交 `.env`、Cookie、Authorization、真实目标数据或 `.statebreaker/runs`。

## 完成定义

- 单元测试覆盖成功路径和失败路径；
- 示例和文档中的命令可以直接运行；
- 插件输出通过公共 Pydantic 模型校验；
- 业务逻辑漏洞必须由攻击前后状态确认，不能只以状态码作为结论；
- 新插件不修改核心注册表。
