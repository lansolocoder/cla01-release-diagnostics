# macOS 应用发布与兼容性诊断工作台

用于本地检查 macOS 应用发布产物与兼容性信息的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m release_workbench --help
python3 -m release_workbench --version
python3 -m release_workbench app-info /path/to/Example.app
python3 -m unittest discover -s tests -v
```

`app-info` 解析 `.app` 应用包结构，向 stdout 输出单行 JSON：

```json
{"bundle_id": "com.example.App", "executable": "App", "info_plist": "Contents/Info.plist", "components": ["Contents/Info.plist", "Contents/MacOS", "Contents/Resources"], "status": "ok"}
```

`bundle_id`、`executable` 取 `Contents/Info.plist`（标准库 `plistlib` 解析的 XML plist）中的 `CFBundleIdentifier`、`CFBundleExecutable` 字符串值，键缺失或非字符串时为 `null`；`info_plist` 恒为 `"Contents/Info.plist"`；`components` 是 `Contents/` 下一级子项相对包根的路径，按字典序升序并去重。存在且可解析 `Info.plist` 时 `status` 为 `"ok"`；文件缺失时为 `"missing-info-plist"`，此时成功退出（0）。路径不存在、不是目录、名称不以 `.app` 结尾、缺少 `Contents` 目录、`Info.plist` 非法或根对象不是字典时，诊断信息写入 stderr（含出错路径），stdout 为空，退出码 2。额外的 CLI 选项同样报错退出。

`compat-report` 在 `app-info` 的包结构信息之上，对照目标 Mac 的 profile 判断应用能否运行：

```bash
python3 -m release_workbench compat-report /path/to/Example.app profile.json
```

profile 是 UTF-8 JSON 对象，仅含两个字段：`os_version` 为点分十进制版本串（每段纯数字、无前导零），`cpu` 为 `"arm64"` 或 `"x86_64"`。profile 文件缺失、JSON 非法、根不是对象、字段非法或含额外字段时，stderr 含 profile 路径、stdout 为空、退出码 2；profile 校验先于 app 校验，两侧皆错时只报告 profile。输出为单行 JSON：

```json
{"bundle_id": "com.example.App", "executable": "Example", "required_os_version": "11.0", "architectures": ["arm64"], "blocks": [], "status": "compatible"}
```

`required_os_version` 取 `LSMinimumSystemVersion` 字符串值，键缺失、非字符串或不合版本语法时为 `null`；`architectures` 为 `Contents/MacOS/<executable>` 的 Mach-O 薄 magic（FEEDFACF/FEEDFACE 按 cputype 为 x86_64/i386，CFFAEDFE/CEFAEDFE 为 arm64/armv7）识别出的架构，去重升序；可执行文件缺失、非普通文件、读取失败、magic 未知或为 FAT（CAFEBABE/BEBAFECA）时为空列表。`blocks` 每项含 `code` 与 `detail`：`required_os_version` 大于目标 `os_version` 时出现 `os-version`（点分段数值比较，共有段相同则段多者大），detail 为 `{"required": ..., "target": ...}`；`architectures` 非空且不含目标 cpu 时出现 `cpu`，detail 为 `{"supported": [...], "target": ...}`。每种 code 至多一项，按 code 升序排列。无 blocks 时 `status` 为 `"compatible"`，否则为 `"blocked"`。app 侧无效输入的诊断与 `app-info` 一致。

`release-diff` 比较两次发布产物，回答“这次改版动了什么”：

```bash
python3 -m release_workbench release-diff /path/to/Old.app /path/to/New.app
```

输出为单行 JSON 对象，字段固定为 `changes`、`missing_in_new`、`added_in_new`、`status`。两侧 `Info.plist` 都存在且可解析时比较 `CFBundleIdentifier`、`CFBundleExecutable`、`LSMinimumSystemVersion`（取值规则同上，键按字典序处理），值不同（含一侧缺失，缺失侧为 `null`）时产生 `{"kind": "metadata", "path": "Contents/Info.plist", "detail": {"key": 键名, "old": 旧值, "new": 新值}}`；任一侧 `Info.plist` 缺失时 metadata 比较整体跳过。`missing_in_new`、`added_in_new` 分别列出仅存在于旧包、仅存在于新包的 `Contents/` 下一级子项相对路径，字典序升序。两侧都存在且都是普通文件的 `Contents/` 下一级子项按原始字节比较（目录、symlink、不可读文件不比较也不产条目），字节不同产生 `{"kind": "modified", "path": ..., "detail": {"old_size": ..., "new_size": ...}}`。`changes` 按 `(kind, path)` 字典序排序。三者皆空时 `status` 为 `"identical"`，否则为 `"changed"`。任一包校验失败沿用 app 侧诊断规则（stderr 含出错路径、stdout 为空、退出码 2）；新包出错时 stderr 同时含新旧两路径，只报告一次，不输出部分报告。

帮助与版本查询入口保持可用；无参数显示帮助，未知参数以非零状态退出。尚未实现签名与信任检查、依赖核对以及更新渠道检查，不会创建或修改业务数据文件。
