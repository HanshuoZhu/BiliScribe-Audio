# Provenance and fidelity

Use a provenance field on structured records even when the final Markdown also carries a visible label.

| Provenance | Markdown form | Evidence |
|---|---|---|
| `COURSE_SPOKEN` | transcript-supported course statement | lesson + timestamp + understood entry ID; coarse times explicitly labeled |
| `COURSE_MATERIAL` | `[课件]` | file + page/slide |
| `TEXTBOOK` | `[课本补充]` | selected chapter ID + confirmed printed page + physical PDF page |
| `AI_DERIVED` | `AI 补解` or `AI 说明` | separate section and input basis |

When two sources disagree, retain both statements with their labels and state the conflict. A source label describes where the evidence came from; it does not make a visual or material claim spoken by the teacher.

For emphasis, use only:

```text
[期末必考] [类型题必考] [以前考过] [重点] [易错题型]
```

For omission events, keep the original quote and timestamp, the best-supported topic, and a status. `DEFERRED` is checked after all selected lessons are available; unresolved items remain visible.

Audio does not accept COURSE_VISUAL. Screen-only information must remain unverified; use Vision when it matters.
