# 贡献指南 / Contributing

欢迎修复 bug、改进来源保真、补充跨平台安装体验与原创示例。先阅读 README 的能力边界和 NOTICE。大型功能先开 issue 解释使用场景，避免添加第二套模型服务或未经验证的自动讲义承诺。

## 本地开发

Python 3.11+，推荐 3.12。克隆后执行 .agents/skills/bili-scribe-audio/scripts/setup.py，再用生成的 .venv Python 运行：

```bash
python -m unittest discover -s tests -v

```

此处 python 应替换为 .venv/Scripts/python.exe 或 .venv/bin/python。测试不需要真实课程、API 密钥、模型下载或 GPU。

## 提交修改

1. 一次 PR 解决一个可说明的问题，给出修改前后行为。
2. 新脚本或改变非平凡行为时，提供能观察结果的离线测试，不只断言措辞。
3. 运行相关测试，记录系统与版本；没有执行的验证应明确说明。
4. 更新受影响的命令、输出或能力文档，保持中文与英文首页的事实一致。

请勿提交课程媒体、真实转录、课件、教材、密钥、Cookie、缓存或模型权重。可使用有权公开的最小合成材料。对于模型理解失败，给出输入、原输出、期望和证据，不把自动校验通过当作准确性证明。

提交贡献表示按 GPL-3.0-or-later 授权贡献；保留现有署名与第三方许可。请尊重 [行为准则](CODE_OF_CONDUCT.md)。English contributions and issue reports are welcome.
