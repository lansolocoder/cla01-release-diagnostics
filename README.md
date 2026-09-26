# macOS 应用发布与兼容性诊断工作台

用于本地检查 macOS 应用发布产物与兼容性信息的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m release_workbench --help
python3 -m release_workbench --version
python3 -m release_workbench inspect-app /path/to/Sample.app
python3 -m unittest discover -s tests -v
```

无参数显示帮助，未知参数以非零状态退出。`inspect-app` 子命令以一个 `.app` 包目录路径作为唯一必选位置参数，读取 `Contents/Info.plist` 中的 `CFBundleIdentifier` 与 `CFBundleExecutable`，并清点 `Contents/Frameworks` 下的 `.framework`、`Contents/PlugIns` 下的 `.plugin`、`Contents/Frameworks` 下的 `.app`，向 stdout 输出一行 JSON：

```json
{"appPath": "/path/to/Sample.app", "bundleIdentifier": "com.example.sample", "executableName": "Sample", "frameworks": ["Alpha"], "plugins": ["Bravo"], "nestedApps": ["Helper"]}
```

三类数组按名称升序去重；Info.plist 缺失或解析失败时对应字段为 `null`，某类目录不存在时对应数组为空。路径不存在、不是目录或缺少 `Contents` 子目录时向 stderr 输出 `{"error": "invalid-bundle"}`；路径不以 `.app` 结尾时输出 `{"error": "not-app-bundle"}`，均以退出码 2 退出且不写 stdout。该命令不会创建或修改任何文件。尚未实现签名与信任检查、依赖与架构核对、发布比较以及更新渠道检查。
