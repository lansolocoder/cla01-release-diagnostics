# macOS 应用发布与兼容性诊断工作台

用于本地检查 macOS 应用发布产物与兼容性信息的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m release_workbench --help
python3 -m release_workbench --version
python3 -m release_workbench app-info /path/to/App.app
python3 -m unittest discover -s tests -v
```

## app-info

解析 `.app` 应用包结构，向 stdout 输出单行 JSON：

```json
{"bundle_id": "com.example.app", "executable": "App", "info_plist": "Contents/Info.plist", "components": ["Contents/Info.plist", "Contents/MacOS"], "status": "ok"}
```

- `bundle_id`、`executable` 分别取 `Contents/Info.plist` 中 `CFBundleIdentifier`、`CFBundleExecutable` 的字符串值；键缺失或非字符串时为 `null`。
- `info_plist` 恒为 `"Contents/Info.plist"`。
- `components` 为 `Contents/` 下一级子项的相对路径，按字符串字典序升序、去重。
- `status` 为 `"ok"`（Info.plist 存在且可解析）或 `"missing-info-plist"`（Info.plist 不存在；此时 `bundle_id`、`executable` 为 `null`，components 仍按实际目录列出）。

以下输入报错，诊断信息写 stderr（含出错路径），stdout 为空，退出码 2：路径不存在、不是目录、名称不以 `.app` 结尾、缺少 `Contents` 目录、Info.plist 不是合法 plist 或其根对象不是字典。额外的 CLI 选项或参数同样报错，不会被静默忽略。成功（含 `missing-info-plist`）退出码 0。

帮助与版本查询入口仍然保留；无参数显示帮助，未知参数以非零状态退出。尚未实现签名与信任检查、依赖与架构核对、发布比较以及更新渠道检查，不会创建业务数据文件。
