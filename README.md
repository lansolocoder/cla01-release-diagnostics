# macOS 应用发布与兼容性诊断工作台

用于本地检查 macOS 应用发布产物与兼容性信息的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m release_workbench --help
python3 -m release_workbench --version
python3 -m release_workbench app-info /path/to/Example.app
python3 -m release_workbench macho-info /path/to/Example.app
python3 -m release_workbench diff-apps /path/to/Old.app /path/to/New.app
python3 -m release_workbench sign-info /path/to/Example.app
python3 -m release_workbench dep-check /path/to/Example.app x86_64
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

`diff-apps` 接收旧包、新包两个位置参数（按此顺序校验），分别按 `app-info`、`macho-info` 的公开规则计算两侧结果后比较，向 stdout 输出单行 JSON：

```json
{"added_components": [], "removed_components": [], "bundle_id_changed": false, "executable_changed": false, "added_architectures": [], "removed_architectures": [], "added_binaries": [], "removed_binaries": [], "dylib_changes": [{"path": "Contents/MacOS/App", "added": ["/usr/lib/libz.1.dylib"], "removed": []}]}
```

`added_components`/`removed_components`、`added_architectures`/`removed_architectures`、`added_binaries`/`removed_binaries` 分别为两侧 components、architectures、binary 相对路径的集合差（新减旧、旧减新），升序；`dylib_changes` 只含两侧都存在且 dylib 集合不同的 binary，按 `path` 升序，其 `added`/`removed` 为该 binary 两侧 dylibs 的集合差。`bundle_id_changed`、`executable_changed` 表示两侧 `CFBundleIdentifier`、`CFBundleExecutable` 是否不等（`Info.plist` 缺失或值非字符串时按 `null` 比较，`null` 与字符串不等也算改变）。两侧完全一致时列表全空、布尔均为 `false`。校验先旧包后新包：任一包路径不存在、非目录、名称不以 `.app` 结尾、缺少 `Contents`，或任一侧 `Info.plist` 非法、包内 Mach-O 畸形时，诊断信息写入 stderr（含出错路径），stdout 为空，退出码 2，不输出部分比较结果。成功时退出码 0、stderr 为空、stdout 恰一行 JSON。

`sign-info` 按 `macho-info` 同样的规则遍历包内 Mach-O（`Contents/MacOS/` 与 `Contents/Resources/`，递归进子目录、跳过符号链接，按原始字节魔数判定），逐个解析 `LC_CODE_SIGNATURE` 负载命令定位 embedded 签名目录（SuperBlob：魔数、长度、索引表条目），向 stdout 输出单行 JSON：

```json
{"binaries": [{"path": "Contents/MacOS/App", "status": "signed", "identifier": "com.example.App", "team_id": "ABCD123456", "entries": ["CodeDirectory", "SignatureSlot"]}], "unsigned_binaries": []}
```

`binaries` 按相对包根、`/` 分隔的 `path` 升序：存在 `LC_CODE_SIGNATURE` 且签名目录完整时 `status` 为 `"signed"`，无 `LC_CODE_SIGNATURE` 时为 `"unsigned"`。`identifier` 取签名目录 CodeDirectory 中的标识字符串；`team_id` 取团队标识（版本低于支持团队标识的 CodeDirectory 或字段缺失时为 `null`）；`entries` 为索引表各槽位类型名称（如 `CodeDirectory`、`InfoSlot`、`RequirementsSlot`、`ResourceDir`、`ApplicationSlot`、`EntitlementsSlot`、`DEREntitlementsSlot`、`SignatureSlot`、`IdentificationSlot`、`TicketSlot`，主 CodeDirectory 与备选 CodeDirectory 槽位都记为 `CodeDirectory`），去重升序。未签名时 `identifier`、`team_id` 为 `null`、`entries` 为空数组，且该相对路径同时进入 `unsigned_binaries`（升序）。包内无 Mach-O 或全部未签名时正常输出并退出 0。包级校验（路径不存在、非目录、名称不以 `.app` 结尾、缺少 `Contents`）与其他命令一致；`LC_CODE_SIGNATURE` 数据偏移或长度越出文件、签名目录魔数非嵌入式签名（`0xfade0cc0`）、目录长度与负载不符、索引表条目或槽位偏移越界、缺少 CodeDirectory 槽位时，诊断信息写入 stderr（含出错文件路径），stdout 为空，退出码 2，不输出部分结果。本命令只解析签名目录结构，不校验证书链与信任状态。

`dep-check` 按 `macho-info` 同样的规则遍历包内 Mach-O（`Contents/MacOS/` 与 `Contents/Resources/`，递归进子目录、跳过符号链接，按原始字节魔数判定），读取各 binary 的架构标签与 `LC_LOAD_DYLIB` 依赖，结合目标架构标签（第二个位置参数，限 `i386`、`ppc`、`x86_64`、`ppc64` 四种），向 stdout 输出单行 JSON：

```json
{"target_arch": "x86_64", "missing_arch_binaries": ["Contents/MacOS/Helper"], "external_dylibs": ["/usr/lib/libSystem.B.dylib"], "missing_dylibs": ["/opt/local/lib/libssl.dylib"], "unresolved_dylibs": ["@rpath/libPlug.dylib"], "status": "missing-deps"}
```

`missing_arch_binaries` 为架构标签不等于目标架构的 binary 相对包根路径，升序；`unresolved_dylibs` 为 `@` 开头（如 `@rpath`、`@loader_path`、`@executable_path`）的依赖，无法静态定位，去重升序；指向包内 `Contents/Frameworks/` 的依赖不计入任何列表；其余绝对路径依赖为外部依赖，进 `external_dylibs`，去重升序。`missing_dylibs` 为外部依赖中宿主上缺失者：`/usr/lib/`、`/System/` 开头视作存在，其余直接查文件系统是否存在，去重升序。`status` 优先级：`missing_dylibs` 非空为 `"missing-deps"`；否则 `missing_arch_binaries` 非空为 `"arch-mismatch"`；否则为 `"ok"`。包内无 Mach-O 时三个列表均为空、`status` 为 `"ok"`。包级校验（路径不存在、非目录、名称不以 `.app` 结尾、缺少 `Contents`）与其他命令一致；目标架构标签非法时诊断信息写入 stderr（含出错值），stdout 为空，退出码 2；包内 Mach-O 畸形沿用 `macho-info` 失败规则（诊断含出错文件路径，stdout 为空，退出码 2，不输出部分结果）。成功时退出码 0、stderr 为空、stdout 恰一行 JSON。

尚未实现签名信任评估以及更新渠道检查，不会创建或修改业务数据文件。
