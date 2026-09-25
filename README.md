# macOS 应用发布与兼容性诊断工作台

用于本地检查 macOS 应用发布产物与兼容性信息的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m release_workbench --help
python3 -m release_workbench --version
python3 -m release_workbench inspect /path/to/App.app
python3 -m release_workbench diagnose /path/to/App.app
python3 -m unittest discover -s tests -v
```

## inspect 子命令

```bash
python3 -m release_workbench inspect <app>
```

读取 `<app>/Contents/Info.plist`（XML 或二进制 plist）与 `<app>/Contents/MacOS/`，向 stdout 输出一个 JSON 对象（退出码 0），不会改动应用包内的任何文件。顶层字段：

- `bundle_path`：应用包绝对路径经 `os.path.realpath` 规范化后的字符串
- `bundle_identifier`、`bundle_name`、`short_version`：分别取 `CFBundleIdentifier`、`CFBundleName`、`CFBundleShortVersionString`，键缺失或值非字符串时为 `null`
- `executable`：取 `CFBundleExecutable` 指向的 `MacOS/` 下文件；未给出时为 `null`，文件不存在、不可执行或为目录时同样为 `null`，并在 `issues` 中记录 `{"code":"executable-missing","path": <文件名>}`
- `executables`：`MacOS/` 下所有普通文件（不递归、不含子目录与符号链接）的相对文件名，按字典序排序
- `issues`：问题列表，每项含 `code`、`path` 两个字符串字段

参数数量不对、路径不存在或不是目录、缺少 `Contents/Info.plist`、plist 无法解析或顶层非字典、缺少 `Contents/MacOS` 或其不是目录时，向 stderr 写一行错误并以退出码 2 退出，stdout 为空。

## diagnose 子命令

```bash
python3 -m release_workbench diagnose <app>
```

在 inspect 相同的包结构与 Info.plist 校验之上，读取应用包的签名与信任状态（macOS 上使用系统命令 `codesign` 与 `spctl`，均为只读操作），向 stdout 输出一个 JSON 对象（退出码 0），不会改动应用包内的任何文件。顶层字段：

- `bundle_path`：应用包绝对路径经 `os.path.realpath` 规范化后的字符串
- `identifier`、`team_identifier`：签名标识与团队标识，无法读取或值非字符串时为 `null`
- `signature_status`：`signed`（签名可验证）、`unsigned`（无签名标识或无签名密封清单）、`damaged`（签名校验失败）、`unsupported`（非 macOS 平台或系统命令不可用）之一
- `trusted`：`true`、`false` 或 `null`（无法评估时，包括 `unsupported` 情形）
- `issues`：问题列表，每项含 `code`、`path` 两个字符串字段；`code` 取 `signature-missing`（对应 unsigned）、`signature-damaged`（对应 damaged）、`trust-denied`（对应 trusted 为 false），`path` 为签名文件相对路径 `Contents/_CodeSignature/CodeResources`，同一文件名至多一条；三种情况仍输出 JSON 且退出码为 0

失败路径（参数数量不对、路径不存在或不是目录、缺少 `Contents/Info.plist`、plist 无法解析或顶层非字典、缺少 `Contents/MacOS` 或其不是目录）与 inspect 一致：向 stderr 写一行错误并以退出码 2 退出，stdout 为空，不生成或修改任何文件。Info.plist 中未列出的键一律忽略。

目前帮助、版本查询、应用包结构盘点与签名信任诊断之外的检查（依赖与架构、发布比较、更新渠道）尚未实现，不会创建业务数据文件。
