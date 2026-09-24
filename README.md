# macOS 应用发布与兼容性诊断工作台

用于本地检查 macOS 应用发布产物与兼容性信息的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m release_workbench --help
python3 -m release_workbench --version
python3 -m release_workbench scan /path/to/Sample.app
python3 -m unittest discover -s tests -v
```

## scan 子命令

`python3 -m release_workbench scan <app路径>` 检查一个 `.app` 产物，成功时向 stdout
输出一行 JSON，字段包括：

- `app_path`：命令行传入的应用包路径原文
- `bundle_identifier` / `bundle_version`：取自 `Contents/Info.plist` 中
  `CFBundleIdentifier`、`CFBundleVersion`，缺失或非字符串时为 `null`
- `executable`：`Contents/MacOS/` 下与 `CFBundleExecutable` 同名的文件路径，
  不存在（或 `CFBundleExecutable` 缺失/非字符串）时为 `null`，并追加
  `missing_executable` issue
- `architectures`：解析 Mach-O 头得到的架构（`arm64`、`x86_64`、`i386`、`ppc`、
  `ppc64`，未知 cputype 以小写十六进制字符串表示）；fat 二进制包含全部架构；
  非 Mach-O 文件为空数组
- `linked_libraries`：二进制中所有 `LC_LOAD_DYLIB` 的 install name 原文
- `issues`：发现的问题列表，每项含 `code` 与 `detail`

所有数组按字典序排序并去重；字段缺失不算错误，退出码仍为 0。路径不是目录、
缺少 `Contents/Info.plist` 或 plist 非法时，stderr 输出含 `invalid_app_package`
的错误信息，退出码为 2，stdout 为空。

尚未实现签名与信任检查、发布比较以及更新渠道检查，不会创建业务数据文件。
