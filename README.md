# macOS 应用发布与兼容性诊断工作台

用于本地检查 macOS 应用发布产物与兼容性信息的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m release_workbench --help
python3 -m release_workbench --version
python3 -m unittest discover -s tests -v
```

## 组件清单：`inspect`

```bash
python3 -m release_workbench inspect <app路径>
```

递归扫描 `.app` 包内 `Contents` 下的全部条目（不跟随符号链接），在 stdout 输出单个 JSON 对象：`app`（规范化后的 .app 根绝对路径）、`root`（固定 `Contents`）、`entries`（`path`/`kind`/`bytes`，按路径码点升序）、`totalFiles`、`totalBytes`。失败时不产生 JSON，stderr 输出一行定位文本：路径无效退出码 2，缺少或不可读 `Contents` 退出码 3，条目不可读或存在特殊文件退出码 4。

无参数显示帮助，未知参数以非零状态退出。尚未实现签名与信任检查、依赖与架构核对、发布比较以及更新渠道检查，不会创建业务数据文件。
