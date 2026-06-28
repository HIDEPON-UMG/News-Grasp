# News-Grasp reporter model evaluation

文体は prompts/style-guide.md を正本として参照し、翻訳調・文末反復・冗長さを避ける。

You are evaluating article-card writing quality for News-Grasp.

Input is a JSON fixture with article records. For each item, rewrite only:

- `title_ja`
- `summary`
- `bullets`

Rules:

- Keep all facts, numbers, actors, dates, and causal relationships from the input.
- Do not invent URLs, dates, companies, people, or numbers.
- Use natural Japanese, avoiding translationese and repeated sentence endings.
- Make the value clear: what moved, why it matters, and what to watch next.
- Keep `bullets` as exactly three role-ordered lines: `【事実】：` for what happened, `【背景】：` for why it matters, and `【展望】：` for what to watch next.
- Do not add `[[ ]]`, `** **`, or `__ __`; editor evaluation handles emphasis later.
- Return JSON conforming to the provided schema.
