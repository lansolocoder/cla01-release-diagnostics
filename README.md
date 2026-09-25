# macOS 应用发布与兼容性诊断工作台

用于本地检查 macOS 应用发布产物与兼容性信息的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m release_workbench --help
python3 -m release_workbench --version
python3 -m release_workbench inspect <app路径>
python3 -m release_workbench verify-signature <app路径> [--team-id <标识>]
python3 -m release_workbench check-deps <app路径> [--require-arch <名称>]
python3 -m unittest discover -s tests -v
```

已提供 `inspect` 子命令：递归扫描 .app 包 `Contents` 下的全部条目（不跟随符号链接），在 stdout 输出单个 JSON 组件清单（`app`、`root`、`entries`、`totalFiles`、`totalBytes`）。

已提供 `verify-signature` 子命令：仅检查 `Contents` 下普通文件中识别出的 Mach-O（魔数 0xfeedfacf、0xcffaedfe、0xcafebabe、0xbebafeca），解析其内嵌代码签名（superblob 0xfade0cc0 中的 code directory 0xfade0c02），在 stdout 输出单个 JSON 报告（`app`、`root`、`issues`、`totalChecked`）。issue 类型：`unsigned`（无内嵌签名）、`mismatch`（identity 不含文件名）、`team-mismatch`（与 `--team-id` 不一致）、`metadata`（签名块解析失败或槽缺失/非法编码）。退出码：无问题 0；仅 unsigned/team-mismatch 为 1；含 mismatch/metadata 为 3；无 Mach-O 为 4；输入路径或 .app 判定失败为 2；缺 Contents 或不可读为 3。

无参数显示帮助，未知参数以非零状态退出。已提供 `check-deps` 子命令：仅检查 `Contents` 下普通文件中识别出的 64 位 Mach-O（含 fat 切片），不跟随符号链接；libs 只含 LC_LOAD_DYLIB、LC_LOAD_WEAK_DYLIB、LC_REEXPORT_DYLIB 加载命令路径，字面前缀（区分大小写）剔除 `/usr/lib/`、`/System/Library/`，切片内按路径升序去重。CPU 名映射 0x01000007→arm64、0x0100000c→x86_64，其他为 `0x` 加八位小写十六进制。未指定 `--require-arch` 时每文件一条记录（fat 的 arch 为 null，libs 为各切片合并去重升序）；指定时每切片一条（arch 为切片 CPU 名），Mach-O 缺该架构切片时追加 arch 为 null、missing 为架构名的记录。stdout 输出单个 JSON（`app`、`root`、`bins`、`totalChecked`、`totalMissing`），bins 按 path 升序、同 path 按 arch 升序（null 最小）。退出码：无缺失（或未指定架构）0；有 missing 1；无 Mach-O 4；`--require-arch` 非法、路径不存在或非 .app 为 2；缺 Contents 或不可读为 3。失败时 stdout 为空、stderr 单行错误、不改动包内文件。尚未实现信任检查、发布比较以及更新渠道检查，不会创建业务数据文件。
