# macOS 应用发布与兼容性诊断工作台

用于本地检查 macOS 应用发布产物与兼容性信息的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m release_workbench --help
python3 -m release_workbench --version
python3 -m release_workbench app-info /path/to/Example.app
python3 -m release_workbench compat-report /path/to/Example.app profile.json
python3 -m unittest discover -s tests -v
```

`app-info` 解析 `.app` 应用包结构，向 stdout 输出单行 JSON：

```json
{"bundle_id": "com.example.App", "executable": "App", "info_plist": "Contents/Info.plist", "components": ["Contents/Info.plist", "Contents/MacOS", "Contents/Resources"], "status": "ok"}
```

`bundle_id`、`executable` 取 `Contents/Info.plist`（标准库 `plistlib` 解析的 XML plist）中的 `CFBundleIdentifier`、`CFBundleExecutable` 字符串值，键缺失或非字符串时为 `null`；`info_plist` 恒为 `"Contents/Info.plist"`；`components` 是 `Contents/` 下一级子项相对包根的路径，按字典序升序并去重。存在且可解析 `Info.plist` 时 `status` 为 `"ok"`；文件缺失时为 `"missing-info-plist"`，此时成功退出（0）。路径不存在、不是目录、名称不以 `.app` 结尾、缺少 `Contents` 目录、`Info.plist` 非法或根对象不是字典时，诊断信息写入 stderr（含出错路径），stdout 为空，退出码 2。额外的 CLI 选项同样报错退出。

`compat-report` 按目标机 profile 判断应用能否运行，向 stdout 输出单行 JSON：

```bash
python3 -m release_workbench compat-report /path/to/Example.app profile.json
```

profile 为 UTF-8 JSON 对象，只允许两个字段：`os_version`（点分十进制版本串，每段纯数字且无前导零）与 `cpu`（仅 `"arm64"` 或 `"x86_64"`）。profile 文件缺失、非法 JSON、根不是对象、字段缺失或非法、出现额外字段时，诊断信息写入 stderr（含 profile 路径），stdout 为空，退出码 2；profile 先于 app 校验，两侧皆错时只报 profile。

报告字段：

- `bundle_id`、`executable`：与 `app-info` 相同；
- `required_os_version`：`LSMinimumSystemVersion` 的字符串值；键缺失、非字符串或不符合版本语法时为 `null`；
- `architectures`：读取 `Contents/MacOS/<executable>` 开头的 Mach-O 薄 magic——`FEEDFACF`/`FEEDFACE` 按 cputype 识别 `x86_64`/`i386`，`CFFAEDFE`/`CEFAEDFE` 识别 `arm64`/`armv7`——去重并按升序排列；可执行文件缺失、不是普通文件、读取失败、magic 未知或为 FAT（`CAFEBABE`/`BEBAFECA`）时为空列表；
- `blocks`：每项含 `code` 与 `detail`。`os-version` 在 `required_os_version` 大于目标 `os_version` 时出现（按点分段比较数值，共有段相同则段数多者大），`detail` 为 `{"required": ..., "target": ...}`；`cpu` 在 `architectures` 非空且不含目标 cpu 时出现，`detail` 为 `{"supported": [...], "target": ...}`。每个 code 至多一项，按 code 升序；
- `status`：无 blocks 为 `"compatible"`，否则为 `"blocked"`。

app 侧无效的情形（路径不存在、非目录、名称不以 `.app` 结尾、缺 `Contents`、`Info.plist` 非法或根非对象）与 `app-info` 一致：stderr 含路径、stdout 空、退出码 2。

帮助与版本查询入口保持可用；无参数显示帮助，未知参数以非零状态退出。尚未实现签名与信任检查、依赖与架构核对、发布比较以及更新渠道检查，不会创建或修改业务数据文件。
