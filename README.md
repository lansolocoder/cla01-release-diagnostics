# macOS 应用发布与兼容性诊断工作台

用于本地检查 macOS 应用发布产物与兼容性信息的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m release_workbench --help
python3 -m release_workbench --version
python3 -m release_workbench app-info /path/to/Example.app
python3 -m release_workbench compare /path/to/Old.app /path/to/New.app
python3 -m unittest discover -s tests -v
```

`app-info` 解析 `.app` 应用包结构，向 stdout 输出单行 JSON：

```json
{"bundle_id": "com.example.App", "executable": "App", "info_plist": "Contents/Info.plist", "components": ["Contents/Info.plist", "Contents/MacOS", "Contents/Resources"], "status": "ok"}
```

`bundle_id`、`executable` 取 `Contents/Info.plist`（标准库 `plistlib` 解析的 XML plist）中的 `CFBundleIdentifier`、`CFBundleExecutable` 字符串值，键缺失或非字符串时为 `null`；`info_plist` 恒为 `"Contents/Info.plist"`；`components` 是 `Contents/` 下一级子项相对包根的路径，按字典序升序并去重。存在且可解析 `Info.plist` 时 `status` 为 `"ok"`；文件缺失时为 `"missing-info-plist"`，此时成功退出（0）。路径不存在、不是目录、名称不以 `.app` 结尾、缺少 `Contents` 目录、`Info.plist` 非法或根对象不是字典时，诊断信息写入 stderr（含出错路径），stdout 为空，退出码 2。额外的 CLI 选项同样报错退出。

`compare` 比较两个 `.app` 包，向 stdout 输出单行 JSON：

```json
{"old": "/path/to/Old.app", "new": "/path/to/New.app", "added": ["Contents/Resources"], "removed": [], "changed": [{"key": "CFBundleVersion", "old": null, "new": "101"}], "status": "differs"}
```

`old`、`new` 原样回显两个路径参数；`added`、`removed` 分别为仅出现在新包、仅出现在旧包的 `Contents/` 一级子项相对包根路径，按字典序升序。`changed` 只比较 `CFBundleIdentifier`、`CFBundleExecutable`、`CFBundleShortVersionString`、`CFBundleVersion`：键缺失或非字符串的一侧以 `null` 参与，仅一侧有字面值（`null`↔字符串）才记入 `changed`，条目按键名字典序升序；两侧均为非 `null` 字面值且不等（如 `"1.0"`→`"2.0"`）不记入 `changed`，而通过 `status` 表示。`status` 取值：`"match"`（`added`、`removed`、`changed` 均空且无双侧不等的键）、`"differs"`（存在差异但无双侧不等的键）、`"conflict"`（至少一个键两侧均非 `null` 且不等；此时 `changed` 与 `added`、`removed` 照常给出）。一侧 `Info.plist` 缺失时组件照常比较，四个键在该侧均视为 `null`。两个包沿用 `app-info` 的全部合法性要求，非法时诊断写 stderr（含出错路径）、stdout 为空、退出码 2；额外 CLI 选项同样报错退出（诊断含出错元素）。成功退出码 0，全程不修改任何文件。

帮助与版本查询入口保持可用；无参数显示帮助，未知参数以非零状态退出。尚未实现签名与信任检查、依赖与架构核对以及更新渠道检查，不会创建或修改业务数据文件。
