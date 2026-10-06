# 快速开始 / Quick start

本页从无需联网的示例开始。命令中的 python 指你选择的 Python 3.11+。Windows 可用 py -3.12，Linux/macOS 可用 python3。不要把尖括号占位内容当作真实输入。

## 项目方式

在仓库根目录安装核心依赖：

```bash
python .agents/skills/bili-scribe-audio/scripts/setup.py
# 需要本地语音转写时才添加 --with-asr
```

Windows PowerShell：

```powershell
$py = '.venv/Scripts/python.exe'
$scripts = '.agents/skills/bili-scribe-audio/scripts'
& $py "$scripts/doctor.py"
& $py "$scripts/evidence.py" import-transcript --manifest examples/text/course_manifest.json --lesson-id L001 --input examples/text/transcript.srt -o .cache/demo/transcripts
& $py "$scripts/evidence.py" prepare --manifest examples/text/course_manifest.json --transcripts .cache/demo/transcripts --mode transcript-only -o .cache/demo/evidence_manifest.json
```

Linux / macOS：

```bash
PY=.venv/bin/python
SCRIPTS=.agents/skills/bili-scribe-audio/scripts
"$PY" "$SCRIPTS/doctor.py"
"$PY" "$SCRIPTS/evidence.py" import-transcript --manifest examples/text/course_manifest.json --lesson-id L001 --input examples/text/transcript.srt -o .cache/demo/transcripts
"$PY" "$SCRIPTS/evidence.py" prepare --manifest examples/text/course_manifest.json --transcripts .cache/demo/transcripts --mode transcript-only -o .cache/demo/evidence_manifest.json
```

当前 Codex 阅读生成的 packet、写 analysis，再 evidence.py record 保存理解并撰写讲义。具体字段见 [接口](../.agents/skills/bili-scribe-audio/references/multimodal.md)。以上两条命令只准备文字，不证明理解完成。

## 全局安装方式

```bash
python install.py
# 自定义位置：python install.py --destination /path/to/codex/skills
# 已安装且确认更新：python install.py --update
```

以安装器输出的目录为准。安装后的 scripts/setup.py 在该 Skill 自身目录创建独立 .venv，所有源码和 requirements 已复制，不依赖原仓库。移动或删除原仓库不会改变已安装 Skill。新开 Codex 对话发现 $bili-scribe-audio。可直接把实际 SKILL.md 绝对路径发给 Codex；不要重复复制到多个发现目录。

安装项目依赖与安装全局 Skill 是两种运行环境，选择一种使用即可；不必各建一份环境。配置放在所用依赖根目录的 .env，系统环境变量优先。示例 .env.example 不含密钥；--update 不复制你的 .env 或模型缓存。

## 真实课程

让当前 Codex 执行 Skill。先生成 manifest 并确认讲次，不要让脚本自行决定课程纳入范围。字幕优先；缺失时才获取音频并转写。本地 ASR 需要 --with-asr，并可能在首次转写时下载模型。兼容云 ASR 另需配置，服务差异可能导致失败，明确 cloud 模式失败会报告，auto 模式可转本地。

本地 SRT/MD/TXT 可以导入；没有时间的文字必须提供已知讲次时长，以 coarse 全讲块保留。不要编造视频位置。现有截图请改用 Vision。

## PPT 与课本

materials.py 支持 PDF/PPTX/DOCX/文本本地提取。旧 .ppt 先转 .pptx 或 PDF。课本先 --textbook 查看章节，由用户 --select 选章，再只提取范围；--book-page-offset 是确认的“印刷页 = PDF 物理页 + 偏移”。扫描件需授权云 OCR 或换用 Vision，不把空提取当成功。

## 验收

```bash
# Windows 用 .venv/Scripts/python.exe；Linux/macOS 用 .venv/bin/python
python .agents/skills/bili-scribe-audio/scripts/validate_course.py outputs/my-course --report outputs/my-course/validation_report.json
```

执行本命令需使用已装依赖的 Python。还要抽查原转录。CLI 帮助见 scripts/<command>.py --help，进阶命令见 [工作流](../.agents/skills/bili-scribe-audio/references/workflow.md)。
