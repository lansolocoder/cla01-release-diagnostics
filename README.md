# macOS 应用发布与兼容性诊断工作台

用于本地检查 macOS 应用发布产物与兼容性信息的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m release_workbench --help
python3 -m release_workbench --version
python3 -m unittest discover -s tests -v
```

当前仅提供帮助与版本查询入口；无参数显示帮助，未知参数以非零状态退出。尚未实现应用包解析、签名与信任检查、依赖与架构核对、发布比较以及更新渠道检查，不会创建业务数据文件。
