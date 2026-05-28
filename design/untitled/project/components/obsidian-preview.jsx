// News Grasp Obsidian/Markdown プレビュー
// frontmatter + 構造化見出し + コードブロック + Callout
// タグ仕様は ../../docs/obsidian-tagging-spec.md

// 1 記事ぶんの動的タグを階層形式で組み立てる
function buildItemTags(item) {
  const tags = [];
  const e = item.entities || {};
  (e.companies || []).forEach(s => tags.push(`co/${s}`));
  (e.countries || []).forEach(s => tags.push(`country/${s}`));
  (e.services  || []).forEach(s => tags.push(`svc/${s}`));
  (e.people    || []).forEach(s => tags.push(`person/${s}`));
  (e.tickers   || []).forEach(s => tags.push(`ticker/${s}`));
  (item.topics     || []).forEach(s => tags.push(`topic/${s}`));
  (item.industries || []).forEach(s => tags.push(`industry/${s}`));
  (item.events     || []).forEach(s => tags.push(`event/${s}`));
  if (item.score >= 85) tags.push('score/高');
  else if (item.score >= 65) tags.push('score/中');
  else tags.push('score/低');
  return tags;
}

// 号全体の frontmatter タグ集合を組み立てる（score は記事ローカルのみ）
function buildAllTags(data) {
  const fixed = ['daily', 'newsletter', 'news-grasp', `issue-${data.issue.no}`];
  const catTags = data.categories.map(c => `cat/${c.id}`);
  const dyn = new Set();
  data.categories.forEach(cat => {
    cat.items.forEach(it => {
      buildItemTags(it).forEach(t => {
        if (t.startsWith('score/')) return;
        dyn.add(t);
      });
    });
  });
  const sorted = Array.from(new Set([...catTags, ...dyn])).sort((a, b) => a.localeCompare(b, 'ja'));
  return [...fixed, ...sorted];
}

function ObsidianPreview({ dark }) {
  const data = window.NEWS_DATA;

  // テーマ：Obsidian Default Dark / Light
  const bg = dark ? '#1E1E1E' : '#FFFFFF';
  const ink = dark ? '#DCDDDE' : '#2E3338';
  const inkDim = dark ? '#999' : '#6C6C6C';
  const accent = dark ? '#A882FF' : '#705DCF';
  const border = dark ? '#3F3F3F' : '#E0E0E0';
  const codebg = dark ? '#262626' : '#F5F5F5';
  const calloutBg = dark ? 'rgba(168,130,255,0.08)' : 'rgba(112,93,207,0.07)';
  const tagbg = dark ? '#373737' : '#EFEFEF';
  const yamlKey = dark ? '#79B8FF' : '#005CC5';
  const yamlStr = dark ? '#9ECBFF' : '#032F62';
  const linkColor = dark ? '#7F6DF2' : '#5E50C2';

  const allTags = buildAllTags(data);
  // H1 直下に並べる chip は数が多くなるので「共通固定＋カテゴリ」までに絞る
  const headerChipTags = allTags.filter(t => !t.includes('/') || t.startsWith('cat/'));

  return (
    <div style={{ background: bg, height: '100%', color: ink, fontFamily: '"Inter", -apple-system, "Segoe UI", sans-serif', WebkitFontSmoothing: 'antialiased', display: 'flex', flexDirection: 'column' }}>
      {/* Obsidian top bar */}
      <div style={{ height: 36, borderBottom: `1px solid ${border}`, flexShrink: 0, display: 'flex', alignItems: 'center', padding: '0 12px', gap: 8, background: dark ? '#262626' : '#FAFAFA' }}>
        <div style={{ display: 'flex', gap: 4 }}>
          <div style={{ width: 12, height: 12, borderRadius: '50%', background: '#FF5F56' }} />
          <div style={{ width: 12, height: 12, borderRadius: '50%', background: '#FFBD2E' }} />
          <div style={{ width: 12, height: 12, borderRadius: '50%', background: '#27C93F' }} />
        </div>
        <div style={{ marginLeft: 16, fontSize: 12, color: inkDim, fontFamily: '"JetBrains Mono", monospace' }}>📓 {data.issue.date}-news-grasp.md</div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 14, fontSize: 11, color: inkDim }}>
          <span>Reading view</span>
          <span style={{ opacity: 0.4 }}>|</span>
          <span style={{ color: accent }}>📑 Outline</span>
        </div>
      </div>

      {/* スクロール領域 */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '28px 60px 60px', maxWidth: 800, margin: '0 auto', width: '100%', boxSizing: 'border-box' }}>
        {/* frontmatter */}
        <div style={{ background: codebg, border: `1px solid ${border}`, borderRadius: 4, padding: '12px 16px', marginBottom: 24, fontFamily: '"JetBrains Mono", monospace', fontSize: 12.5, lineHeight: 1.7 }}>
          <div style={{ color: inkDim }}>---</div>
          <div><span style={{ color: yamlKey }}>title</span>: <span style={{ color: yamlStr }}>"News Grasp #{data.issue.no} — 五つの視点で、今日を掴む。"</span></div>
          <div><span style={{ color: yamlKey }}>date</span>: <span style={{ color: yamlStr }}>{data.issue.date}</span></div>
          <div><span style={{ color: yamlKey }}>issue</span>: <span style={{ color: yamlStr }}>{data.issue.no}</span></div>
          <div><span style={{ color: yamlKey }}>tags</span>:</div>
          {allTags.map(t => (
            <div key={t} style={{ paddingLeft: 16 }}>- <span style={{ color: yamlStr }}>{t}</span></div>
          ))}
          <div><span style={{ color: yamlKey }}>categories</span>: [{data.categories.map(c => <span key={c.id}><span style={{ color: yamlStr }}>{c.id}</span>{c.id !== 'game' && ', '}</span>)}]</div>
          <div><span style={{ color: yamlKey }}>theme</span>: <span style={{ color: yamlStr }}>"金利の天井とAIの底入れ"</span></div>
          <div style={{ color: inkDim }}>---</div>
        </div>

        {/* H1 */}
        <h1 style={{ fontSize: 28, fontWeight: 800, margin: '0 0 8px', borderBottom: `1px solid ${border}`, paddingBottom: 8 }}>News Grasp #{data.issue.no} — 五つの視点で、今日を掴む。</h1>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 18 }}>
          {headerChipTags.map(t => (
            <span key={t} style={{ background: tagbg, color: accent, padding: '2px 8px', borderRadius: 10, fontSize: 11, fontFamily: '"JetBrains Mono", monospace' }}>#{t}</span>
          ))}
          <span style={{ alignSelf: 'center', fontSize: 11, color: inkDim, fontFamily: '"JetBrains Mono", monospace' }}>
            +{allTags.length - headerChipTags.length} more（frontmatter 参照）
          </span>
        </div>

        {/* Callout */}
        <div style={{ background: calloutBg, borderLeft: `3px solid ${accent}`, padding: '12px 16px', borderRadius: 4, marginBottom: 24 }}>
          <div style={{ color: accent, fontWeight: 700, fontSize: 13, marginBottom: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
            <span>📌</span> Today's Theme
          </div>
          <div style={{ fontSize: 13, lineHeight: 1.65 }}>金利の天井とAIの底入れ — 米利下げ示唆と日銀利上げ観測の同時進行、AIエージェント実需化の二大潮流。</div>
        </div>

        {/* TOC */}
        <h2 style={{ fontSize: 20, fontWeight: 700, margin: '24px 0 10px', borderBottom: `1px solid ${border}`, paddingBottom: 4 }}>📑 目次</h2>
        <ul style={{ paddingLeft: 22, margin: '0 0 24px', fontSize: 13, lineHeight: 1.9 }}>
          {data.categories.map((c, i) => (
            <li key={c.id}><span style={{ color: linkColor, textDecoration: 'underline', textDecorationStyle: 'dashed', textDecorationColor: linkColor + '60' }}>[[#{i+1}-{c.name}|{c.glyph} {c.name} ({c.nameEn})]]</span></li>
          ))}
          <li><span style={{ color: linkColor, textDecoration: 'underline', textDecorationStyle: 'dashed', textDecorationColor: linkColor + '60' }}>[[#考察|§ 本日のテーマ考察]]</span></li>
        </ul>

        {/* カテゴリ */}
        {data.categories.map((cat, ci) => (
          <section key={cat.id} style={{ marginBottom: 32 }}>
            <h2 style={{ fontSize: 22, fontWeight: 700, margin: '32px 0 6px', borderBottom: `1px solid ${border}`, paddingBottom: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ color: cat.accent, fontFamily: '"JetBrains Mono", monospace' }}>{cat.glyph}</span>
              {ci+1}. {cat.name}
              <span style={{ marginLeft: 'auto', fontSize: 11, color: inkDim, fontFamily: '"JetBrains Mono", monospace', fontWeight: 400 }}>{cat.items.length} items</span>
            </h2>
            <div style={{ background: tagbg, padding: '8px 12px', borderRadius: 4, fontSize: 12.5, color: inkDim, fontStyle: 'italic', marginBottom: 16 }}>
              <span style={{ color: accent, fontWeight: 600, marginRight: 6 }}>summary:</span>{cat.summary}
            </div>

            {cat.items.map((it, idx) => (
              <div key={idx} style={{ marginBottom: 14, paddingLeft: 14, borderLeft: `2px solid ${border}` }}>
                <h3 style={{ fontSize: 14, fontWeight: 700, margin: '0 0 4px', display: 'flex', alignItems: 'baseline', gap: 8 }}>
                  <span style={{ fontFamily: '"JetBrains Mono", monospace', color: cat.accent, fontSize: 12 }}>[{String(it.score).padStart(2,'0')}]</span>
                  {it.title}
                </h3>
                <div style={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 11, color: inkDim, marginBottom: 6 }}>
                  📅 {data.issue.date} {it.time} · 📰 {it.source}
                </div>
                {(() => {
                  const itemTags = buildItemTags(it);
                  if (itemTags.length === 0) return null;
                  return (
                    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 6 }}>
                      {itemTags.map(t => (
                        <span key={t} style={{ background: tagbg, color: accent, padding: '1px 6px', borderRadius: 8, fontSize: 10, fontFamily: '"JetBrains Mono", monospace' }}>#{t}</span>
                      ))}
                    </div>
                  );
                })()}
                <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
                  <NewsThumb thumb={it.thumb} accent={cat.accent} size="md" />
                  <p style={{ fontSize: 12.5, lineHeight: 1.75, margin: 0, color: ink, flex: 1 }}>{it.body}</p>
                </div>
              </div>
            ))}
          </section>
        ))}

        {/* 考察 */}
        <h2 style={{ fontSize: 22, fontWeight: 700, margin: '40px 0 6px', borderBottom: `1px solid ${border}`, paddingBottom: 6 }}>§ 本日のテーマ考察</h2>
        <div style={{ fontSize: 12, color: inkDim, fontStyle: 'italic', marginBottom: 14 }}>{data.reflection.subtitle}</div>
        {data.reflection.body.map((p, i) => (
          <p key={i} style={{ fontSize: 13.5, lineHeight: 1.85, margin: '0 0 12px' }}>{p}</p>
        ))}

        <div style={{ background: calloutBg, borderLeft: `3px solid ${accent}`, padding: '12px 16px', borderRadius: 4, marginTop: 18 }}>
          <div style={{ color: accent, fontWeight: 700, fontSize: 13, marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
            <span>🔗</span> Related Issues
          </div>
          {data.reflection.related.map((r, i) => (
            <div key={i} style={{ fontSize: 12.5, lineHeight: 1.7 }}>
              <span style={{ fontFamily: '"JetBrains Mono", monospace', color: inkDim, marginRight: 8 }}>{r.date}</span>
              <span style={{ color: linkColor, textDecoration: 'underline', textDecorationStyle: 'dashed', textDecorationColor: linkColor + '60' }}>[[{r.title}]]</span>
            </div>
          ))}
        </div>

        <div style={{ marginTop: 36, paddingTop: 14, borderTop: `1px solid ${border}`, fontSize: 11, color: inkDim, fontFamily: '"JetBrains Mono", monospace' }}>
          ← [[{data.issue.no - 1}-news-grasp]] | [[{data.issue.no + 1}-news-grasp]] →
        </div>
      </div>
    </div>
  );
}

window.ObsidianPreview = ObsidianPreview;
