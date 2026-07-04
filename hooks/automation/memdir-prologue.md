# memdir prologue

Rules:

- Use the listed files as read-only context unless a notify/extractor path is explicitly updating them.
- Use recalled memory only when it directly applies to the current request.
- If the request says to ignore memory, ignore the listed files.
- `manifest.json` is the compact project index; `topics/*.json` is the source-of-truth memory store.
- Use injected recalled memory summaries before opening topic JSON files, and do not reread the same topic in one turn unless the summary is insufficient or the file changed.
- If a topic JSON file appears garbled or misdecoded, read it explicitly as UTF-8.
- The SQLite vector index is an auxiliary retrieval index only; do not treat it as canonical content.
- If new memory is needed, prefer updating an existing topic JSON file and keep `manifest.json` compact.
- For memdir settings requests, prioritize script files or this prologue first; touch project-memory topic JSON only when the user explicitly asks for them or when a script/prologue change would break a reference.
- End with `현재 답변은 메모리를 읽어 답변했습니다.` only when recalled memory is a direct reason/source for the answer, not merely because a memdir topic was read or available.
