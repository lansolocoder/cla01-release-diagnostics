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

`arch-check` 核对可执行文件实际架构与 `Info.plist` 中 `LSEntry.archs` 声明的一致性，成功时向 stdout 输出单行 JSON：

```json
{"executable": "Contents/MacOS/App", "declared_archs": ["arm64", "x86_64"], "actual_archs": ["arm64", "x86_64"], "match": true}
```

`executable` 是 `Contents/MacOS/` 下声明的可执行文件相对包根的路径；`declared_archs` 取自 plist 的 `LSEntry.archs` 数组——元素须为小写字符串且仅限 `x86_64`、`arm64`、`universal`，否则诊断含 plist 路径、退出码 2；无该键（或为空数组）时声明为空。实际架构直接从可执行文件的 Mach-O 头部读取：单架构 magic `0xfeedfacf`/`0xfeedface`（按文件字节序读 cputype）与大端 fat 头 magic `0xcafebabe`/`0xcafebabf`；cputype `0x01000007` 记 `x86_64`、`0x0100000c` 记 `arm64`，其他 cputype 不进入输出（实际仅含其他 cputype 或无切片时 `actual_archs` 为空数组），magic 不属于上述四种时 stderr 含可执行文件路径、退出码 2。核对规则：声明为空则无需比对（`match` 恒为 true）；声明含 `universal` 时实际切片数须 ≥ 2；否则每个声明架构都须出现在实际集合中。两个架构数组均按字典序升序去重。路径/包结构/plist/可执行文件存在性等校验沿用 `app-info` 同一套规则（此命令中 `Info.plist` 缺失同样为错误，退出码 2）。任何失败均 stdout 为空、退出码 2，且不会创建或修改文件；额外 CLI 选项沿用现有错误行为。

帮助与版本查询入口保持可用；无参数显示帮助，未知参数以非零状态退出。尚未实现签名与信任检查、依赖检查、发布比较以及更新渠道检查，不会创建或修改业务数据文件。
