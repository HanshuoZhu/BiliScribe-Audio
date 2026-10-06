# 文字课程工作流

使用依赖根目录 .venv 的 Python，scripts 指本 Skill 的 scripts 目录。候选 manifest 默认 pending；只处理用户确认的讲次。

```text
bili_fetch.py manifest <BV/URL...> -o outputs/demo/course_manifest.json
bili_fetch.py subs outputs/demo/course_manifest.json -o transcripts
bili_fetch.py audio outputs/demo/course_manifest.json -o downloads
transcribe.py downloads --manifest outputs/demo/course_manifest.json -o transcripts
evidence.py prepare --manifest outputs/demo/course_manifest.json --transcripts transcripts --mode transcript-only -o .cache/demo/evidence_manifest.json
```

音频命令只用于缺字幕的讲次。已有 SRT 可 evidence.py import-transcript --manifest <manifest> --lesson-id L001 --input <srt> -o transcripts；无时间 TXT/MD 需要已知真实讲次时长，会形成 coarse 块。

当前 Codex 读包、写 analysis.json，执行 evidence.py record --evidence <evidence_manifest> --analysis <analysis.json> -o outputs/demo/understanding_manifest.json，然后 evidence.py check --evidence <evidence_manifest> --understanding <understanding_manifest>。

理解后 materials.py <PPTX/PDF/DOCX> -o outputs/demo/课件笔记。课本先 materials.py <PDF> --textbook --manifest-output outputs/demo/textbook_manifest.json；确认章节后 --select 1,2；印刷页偏移需确认才用 --book-page-offset。--pages 10-20 仅为选中物理页范围的写法示例。

写资料后 validate_course.py outputs/demo --report outputs/demo/validation_report.json，再对照转录做语义抽查。参数 --help 提供实际接口，不在本版使用视频抽帧或图片来源。
