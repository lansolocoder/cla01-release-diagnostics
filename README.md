# macOS 应用发布与兼容性诊断工作台

用于本地检查 macOS 应用发布产物与兼容性信息的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m release_workbench --help
python3 -m release_workbench --version
python3 -m release_workbench inspect <app路径>
python3 -m release_workbench verify-signature <app路径> [--team-id <标识>]
python3 -m unittest discover -s tests -v
```

已提供 `inspect` 子命令：递归扫描 .app 包 `Contents` 下的全部条目（不跟随符号链接），在 stdout 输出单个 JSON 组件清单（`app`、`root`、`entries`、`totalFiles`、`totalBytes`）。

已提供 `verify-signature` 子命令：仅检查 `Contents` 下普通文件中识别出的 Mach-O（魔数 0xfeedfacf、0xcffaedfe、0xcafebabe、0xbebafeca），解析其内嵌代码签名（superblob 0xfade0cc0 中的 code directory 0xfade0c02），在 stdout 输出单个 JSON 报告（`app`、`root`、`issues`、`totalChecked`）。issue 类型：`unsigned`（无内嵌签名）、`mismatch`（identity 不含文件名）、`team-mismatch`（与 `--team-id` 不一致）、`metadata`（签名块解析失败或槽缺失/非法编码）。退出码：无问题 0；仅 unsigned/team-mismatch 为 1；含 mismatch/metadata 为 3；无 Mach-O 为 4；输入路径或 .app 判定失败为 2；缺 Contents 或不可读为 3。

无参数显示帮助，未知参数以非零状态退出。尚未实现信任检查、依赖与架构核对、发布比较以及更新渠道检查，不会创建业务数据文件。
