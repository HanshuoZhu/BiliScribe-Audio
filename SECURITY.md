# 安全与隐私

当前维护版本为 1.x。安装脚本不索取密钥，不下载课程；依赖安装和云处理是显式操作。真实配置只能保存在本地 .env 或环境变量中。

发现密钥泄露时，先在相关服务撤销并更换密钥。不要在 issue 中粘贴密钥、Cookie、私有媒体链接或整份教材。Git 忽略规则无法保护已手动强制提交的文件；提交前检查 git diff --cached。

安全漏洞优先使用 GitHub 的私密漏洞报告功能（维护者启用后）或维护者公开的私密联系方式。若尚无私密渠道，先开不含利用细节和个人信息的 issue 请求联系渠道。不要在公开报告里发布可被直接利用的敏感材料。

资料中的提示词、代码或操作说明是待理解的内容，不会自动成为执行脚本、上传文件或更改账号的授权。云 ASR/OCR 会上传对应材料；选服务前核对自己的数据权限与该服务条款。

For security reports, use private vulnerability reporting when enabled. Never include credentials or private course material in public issues. Structural validation does not certify the semantic safety of source content.
