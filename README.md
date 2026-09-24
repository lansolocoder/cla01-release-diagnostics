# macOS 应用发布与兼容性诊断工作台

用于本地检查 macOS 应用发布产物与兼容性信息的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m release_workbench --help
python3 -m release_workbench --version
python3 -m release_workbench /Applications/Safari.app
python3 -m unittest discover -s tests -v
```

应用包检查：传入一个 `.app` 包目录路径，将 JSON 结果输出到 stdout，字段含
`bundle`、`bundleIdentifier`、`executable`、`frameworks`、`plugins`、`issues`；
一次只接受一个位置参数。路径不存在、不是目录或不以 `.app` 结尾时，错误写入
stderr 并以状态码 2 退出。尚未实现签名与信任检查、依赖与架构核对、发布比较
以及更新渠道检查，不会创建业务数据文件。
