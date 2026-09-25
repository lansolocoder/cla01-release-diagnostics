# macOS 应用发布与兼容性诊断工作台

用于本地检查 macOS 应用发布产物与兼容性信息的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m release_workbench --help
python3 -m release_workbench --version
python3 -m release_workbench app-info /path/to/Example.app
python3 -m release_workbench macho-info /path/to/Example.app
python3 -m unittest discover -s tests -v
```

`app-info` 解析 `.app` 应用包结构，向 stdout 输出单行 JSON：

```json
{"bundle_id": "com.example.App", "executable": "App", "info_plist": "Contents/Info.plist", "components": ["Contents/Info.plist", "Contents/MacOS", "Contents/Resources"], "status": "ok"}
```

`bundle_id`、`executable` 取 `Contents/Info.plist`（标准库 `plistlib` 解析的 XML plist）中的 `CFBundleIdentifier`、`CFBundleExecutable` 字符串值，键缺失或非字符串时为 `null`；`info_plist` 恒为 `"Contents/Info.plist"`；`components` 是 `Contents/` 下一级子项相对包根的路径，按字典序升序并去重。存在且可解析 `Info.plist` 时 `status` 为 `"ok"`；文件缺失时为 `"missing-info-plist"`，此时成功退出（0）。路径不存在、不是目录、名称不以 `.app` 结尾、缺少 `Contents` 目录、`Info.plist` 非法或根对象不是字典时，诊断信息写入 stderr（含出错路径），stdout 为空，退出码 2。额外的 CLI 选项同样报错退出。

帮助与版本查询入口保持可用；无参数显示帮助，未知参数以非零状态退出。

`macho-info` 遍历应用包 `Contents/MacOS/` 与 `Contents/Resources/` 的第一级子项（递归进子目录，符号链接跳过不跟随），按文件原始字节判定 Mach-O 魔数，读取 `LC_LOAD_DYLIB` 命令中的 dylib 路径，向 stdout 输出单行 JSON：

```json
{"architectures": ["x86_64"], "binaries": [{"path": "Contents/MacOS/App", "bits": 64, "endian": "little", "dylibs": ["/usr/lib/libSystem.B.dylib"]}]}
```

`architectures` 为架构标签去重升序（32 位小端 `i386`、32 位大端 `ppc`、64 位小端 `x86_64`、64 位大端 `ppc64`）；`binaries` 按相对包根、`/` 分隔的 `path` 升序，同文件 dylib 去重升序。未发现 Mach-O 时两数组为空，仍成功退出（0）。Mach-O 头截断、dylib 命令畸形（cmdsize 小于命令头、越出文件、偏移非法）或命令版本非 0 时，诊断信息写入 stderr（含出错文件路径），stdout 为空，退出码 2；包级校验（路径不存在、非目录、名称不以 `.app` 结尾、缺少 `Contents`）与 `app-info` 一致，`Info.plist` 缺失或非法不影响本命令。

尚未实现签名与信任检查、依赖与架构核对、发布比较以及更新渠道检查，不会创建或修改业务数据文件。
