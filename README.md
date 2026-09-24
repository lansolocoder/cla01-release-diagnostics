# macOS 应用发布与兼容性诊断工作台

用于本地检查 macOS 应用发布产物与兼容性信息的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m release_workbench --help
python3 -m release_workbench --version
python3 -m release_workbench inspect /path/to/Sample.app
python3 -m unittest discover -s tests -v
```

`inspect` 接受一个 `.app` 包目录路径，将 JSON 报告输出到 stdout，字段为 `bundle`、`bundleIdentifier`、`executable`、`frameworks`、`plugins`、`issues`；路径无效时以退出码 2 失败且不输出 JSON。其余诊断能力（签名与信任检查、依赖与架构核对、发布比较、更新渠道检查）尚未实现，不会创建业务数据文件。
