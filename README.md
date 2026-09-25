# macOS 应用发布与兼容性诊断工作台

用于本地检查 macOS 应用发布产物与兼容性信息的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m release_workbench --help
python3 -m release_workbench --version
python3 -m release_workbench app-info /path/to/Example.app
python3 -m release_workbench arch-check /path/to/Example.app
python3 -m unittest discover -s tests -v
```

`app-info` 解析 `.app` 应用包结构，向 stdout 输出单行 JSON：

```json
{"bundle_id": "com.example.App", "executable": "App", "info_plist": "Contents/Info.plist", "components": ["Contents/Info.plist", "Contents/MacOS", "Contents/Resources"], "status": "ok"}
```

`bundle_id`、`executable` 取 `Contents/Info.plist`（标准库 `plistlib` 解析的 XML plist）中的 `CFBundleIdentifier`、`CFBundleExecutable` 字符串值，键缺失或非字符串时为 `null`；`info_plist` 恒为 `"Contents/Info.plist"`；`components` 是 `Contents/` 下一级子项相对包根的路径，按字典序升序并去重。存在且可解析 `Info.plist` 时 `status` 为 `"ok"`；文件缺失时为 `"missing-info-plist"`，此时成功退出（0）。路径不存在、不是目录、名称不以 `.app` 结尾、缺少 `Contents` 目录、`Info.plist` 非法或根对象不是字典时，诊断信息写入 stderr（含出错路径），stdout 为空，退出码 2。额外的 CLI 选项同样报错退出。

`arch-check` 核对包内可执行文件与架构声明的一致性，向 stdout 输出单行 JSON：

```json
{"executable": "Contents/MacOS/App", "declared_archs": ["arm64", "x86_64"], "actual_archs": ["arm64", "x86_64"], "match": true}
```

`executable` 是 `CFBundleExecutable` 声明的可执行文件相对包根的路径（`Contents/MacOS/` 下）；`declared_archs` 取 `Info.plist` 的 `LSEntry.archs` 数组（元素须为小写字符串，取值限于 `x86_64`、`arm64`、`universal`，无该键时为空）；`actual_archs` 从可执行文件的 Mach-O 头部读取（支持单架构与 fat 头，未知 cputype 记为 `other` 且不计入输出），两者均按字典序升序去重。声明为空时 `match` 恒为 `true`；声明含 `universal` 时要求实际架构数不少于 2；否则声明的每个架构都须出现在实际集合中。包校验规则与 `app-info` 相同；`Info.plist` 缺失或非法、`CFBundleExecutable` 缺失或非字符串、声明的可执行文件缺失或非普通文件、架构声明非法、可执行文件不是可识别的 Mach-O 时，诊断写入 stderr（含出错路径），stdout 为空，退出码 2。

帮助与版本查询入口保持可用；无参数显示帮助，未知参数以非零状态退出。尚未实现签名与信任检查、发布比较以及更新渠道检查，不会创建或修改业务数据文件。
