# macOS 应用发布与兼容性诊断工作台

用于本地检查 macOS 应用发布产物与兼容性信息的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m release_workbench --help
python3 -m release_workbench --version
python3 -m release_workbench inspect /path/to/Demo.app
python3 -m unittest discover -s tests -v
```

`inspect <app>` 读取应用包的 `Contents/Info.plist`（XML 或二进制 plist）与 `Contents/MacOS/`，把结构盘点结果作为 JSON 对象写到 stdout，不改动应用包内任何文件。JSON 顶层字段为 `bundle_path`、`bundle_identifier`、`bundle_name`、`short_version`、`executable`、`executables`、`issues`。参数错误、路径无效或包结构缺失时向 stderr 写一行并以状态码 2 退出。
