# 发布前验证记录

日期：2026-10-06；本地 Windows，Python 3.12.14。

7 个公开安装/文字接口测试通过。

两版 Skill 均通过 quick_validate。Markdown 本地链接、Python 语法与工作流 YAML 已检查。已安装 Skill 的源码完整复制、移开仓库后的入口运行、更新保留 .env、独立配置加载、时间与引用、输入改变后失效都经离线检查。

Vision 的入口、参数、结果三张合成图片由当前 Codex 实际打开并核对；此为自制场景，不是实际课程或软件基准。没有真实 B 站网络获取、真实云 ASR、完整长课程模型质量或实机操作验证。macOS 未本地测试，Windows/Ubuntu CI 结果需首次远端运行确认。

测试依赖版本：

- requests 2.34.2
- pypdf 6.19.0
- imageio-ffmpeg 0.6.0
