# macOS 应用发布与兼容性诊断工作台

用于本地检查 macOS 应用发布产物与兼容性信息的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m release_workbench --help
python3 -m release_workbench --version
python3 -m release_workbench scan /path/to/App.app
python3 -m unittest discover -s tests -v
```

## scan 子命令

`python3 -m release_workbench scan <app路径>` 检查一个 `.app` 产物，成功时向 stdout 输出一行 JSON，字段包括：

- `app_path`：传入的路径原文
- `bundle_identifier` / `bundle_version` / `executable`：取自 `Contents/Info.plist` 与 `Contents/MacOS/`，缺失或无法确定时为 `null`
- `architectures`：由 Mach-O 头（含通用二进制）解析出的 CPU 架构（arm64、x86_64、i386、ppc、ppc64，未知值为十六进制小写字符串），非 Mach-O 时为空数组
- `linked_libraries`：`LC_LOAD_DYLIB` 的 install name 原文
- `issues`：诊断问题，例如可执行文件缺失（`missing_executable`）

数组按字典序排序去重；缺失字段不算错误。路径不是目录、缺少 `Contents/Info.plist` 或 plist 非法时，stderr 输出含 `invalid_app_package` 的错误并以退出码 2 退出，stdout 为空。`scan` 不接受额外选项。

后续仍计划实现签名与信任检查、依赖与架构核对、发布比较以及更新渠道检查，不会创建业务数据文件。
