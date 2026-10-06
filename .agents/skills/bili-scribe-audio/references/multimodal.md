# 当前 Codex 的文字理解接口

evidence.py prepare --mode transcript-only 输出带身份、时间、转录 ID、粗略时间警告、输入指纹的材料包；Python 不推理。当前 Codex 阅读并写以下 analysis.json，再用 evidence.py record 保存。

```json
{
  "packet_id": "L001-T0001",
  "mode": "transcript-only",
  "status": "complete",
  "summary": "替换为实际理解",
  "reviewed_frame_ids": [],
  "entries": [{
    "entry_id": "L001-T0001-E1",
    "kind": "concept",
    "text": "保持原意的理解",
    "provenance": "COURSE_SPOKEN",
    "timestamp": 0,
    "transcript_ids": ["T00001"],
    "frame_ids": [],
    "quote": "来自实际转录的逐字片段"
  }],
  "operations": [],
  "contradictions": [],
  "gaps": []
}
```

ID 必须取实际 packet，不照抄占位示例。status 可为 incomplete；不清楚时保留 gaps。reviewed_frame_ids/frame_ids 必须为空，不能提交 COURSE_VISUAL。quote 要逐字支持，时间在该转录范围内。全讲 coarse 时间不能冒充精确句级定位；讲义注明粗略。

record 与 check 校验结构和来源，不能证明原意理解正确。更新转录后指纹失效，需要重读。完成后再融合 PPT/课本文字，COURSE_MATERIAL/TEXTBOOK/AI_DERIVED 在最终讲义中分开标注。
