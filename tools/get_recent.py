import json, datetime, sys
cutoff = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))) - datetime.timedelta(days=7)
recent = []
path = r"C:\Users\hidek\Obsidian\New's Grasp\News-Grasp\data\articles.jsonl"
with open(path, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            seen = obj.get('seen_at', '')
            if seen:
                dt = datetime.datetime.fromisoformat(seen)
                if dt > cutoff:
                    recent.append({
                        'title': obj.get('title', ''),
                        'url': obj.get('url', ''),
                        'url_norm': obj.get('url_norm', ''),
                        'seen_at': seen,
                        'genre': obj.get('genre', '')
                    })
        except Exception as e:
            pass
print("Total recent (7 days):", len(recent))
for r in recent:
    print(json.dumps(r, ensure_ascii=False))
