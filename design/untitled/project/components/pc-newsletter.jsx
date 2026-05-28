// News Grasp PCメルマガ本文（HTML版）
// 横長レイアウト：ヘッダー＋カテゴリチップ＋3カラム＋テーマ考察

function PCNewsletter({ dark, expandedNews, toggleExpand, starred, toggleStar, read, toggleRead, collapsedCats, toggleCat, filterCats }) {
  const data = window.NEWS_DATA;
  const visibleCats = data.categories.filter(c => filterCats.includes(c.id));

  const bg = dark ? '#0B0E1A' : '#F5F2EC';
  const surface = dark ? '#11162B' : '#FFFEFB';
  const surface2 = dark ? '#171D36' : '#FAF6EE';
  const ink = dark ? '#E8E5DD' : '#11131A';
  const inkDim = dark ? '#9C9A92' : '#5C5A52';
  const border = dark ? 'rgba(255,255,255,0.08)' : 'rgba(20,20,20,0.08)';
  const accent = dark ? '#FFD17A' : '#9D6FD3';

  return (
    <div style={{ background: bg, minHeight: '100%', color: ink, fontFamily: '"Noto Serif JP", "Yu Mincho", "游明朝", serif', WebkitFontSmoothing: 'antialiased' }}>
      {/* ───── Masthead ───── */}
      <div style={{ padding: '40px 56px 24px', borderBottom: `1px solid ${border}`, display: 'flex', alignItems: 'flex-end', gap: 32 }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
            <div style={{ width: 28, height: 28, borderRadius: 4, background: accent, display: 'flex', alignItems: 'center', justifyContent: 'center', color: dark ? '#0B0E1A' : '#fff', fontFamily: '"JetBrains Mono", monospace', fontSize: 14, fontWeight: 700 }}>NG</div>
            <div style={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 11, letterSpacing: 2, color: inkDim, textTransform: 'uppercase' }}>News Grasp · {data.issue.edition}</div>
          </div>
          <div style={{ fontSize: 56, fontWeight: 900, letterSpacing: -1, lineHeight: 1, marginBottom: 10 }}>
            <span style={{ fontFamily: '"JetBrains Mono", monospace', color: accent, fontSize: 36, fontWeight: 700, marginRight: 16, verticalAlign: 'middle' }}>#{data.issue.no}</span>
            五つの視点で、<br />
            <span style={{ background: dark ? 'linear-gradient(180deg,transparent 60%, rgba(255,209,122,.25) 60%)' : 'linear-gradient(180deg,transparent 60%, rgba(157,111,211,.22) 60%)' }}>今日を掴む。</span>
          </div>
          <div style={{ fontSize: 14, color: inkDim, marginTop: 14, fontFamily: '"JetBrains Mono", monospace', letterSpacing: 1 }}>
            {data.issue.date}（{data.issue.weekday}）· {data.categories.length} categories · {data.categories.reduce((a,c)=>a+c.items.length,0)} stories
          </div>
        </div>
        <div style={{ borderLeft: `1px solid ${border}`, paddingLeft: 24, width: 280 }}>
          <div style={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 10, letterSpacing: 1.5, color: inkDim, marginBottom: 8 }}>TODAY'S THEME</div>
          <div style={{ fontSize: 16, fontWeight: 700, lineHeight: 1.5, marginBottom: 8 }}>金利の天井とAIの底入れ</div>
          <div style={{ fontSize: 12, color: inkDim, lineHeight: 1.6 }}>米利下げ示唆と日銀利上げ観測の同時進行、AIエージェント実需化の二大潮流。</div>
        </div>
      </div>

      {/* ───── カテゴリチップ ───── */}
      <div style={{ padding: '20px 56px', display: 'flex', gap: 8, flexWrap: 'wrap', borderBottom: `1px solid ${border}`, alignItems: 'center' }}>
        <div style={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 10, color: inkDim, letterSpacing: 1.5, marginRight: 8 }}>FILTER</div>
        {data.categories.map(c => {
          const on = filterCats.includes(c.id);
          return (
            <button key={c.id} onClick={() => toggleCat(c.id, 'filter')}
              style={{
                border: `1px solid ${on ? c.accent : border}`, borderRadius: 999, padding: '6px 14px',
                background: on ? (dark ? c.accent + '22' : c.accent + '15') : 'transparent',
                color: on ? c.accent : inkDim, cursor: 'pointer',
                fontFamily: '"JetBrains Mono", monospace', fontSize: 11, letterSpacing: 0.5,
                display: 'flex', alignItems: 'center', gap: 8,
              }}>
              <span style={{ fontSize: 13 }}>{c.glyph}</span>
              {c.name} <span style={{ opacity: 0.6 }}>{c.items.length}</span>
            </button>
          );
        })}
      </div>

      {/* ───── 各カテゴリ ───── */}
      <div style={{ padding: '8px 56px 40px' }}>
        {visibleCats.map((cat, ci) => {
          const collapsed = collapsedCats.includes(cat.id);
          return (
            <section key={cat.id} style={{ borderBottom: `1px solid ${border}`, padding: '32px 0' }}>
              {/* セクションヘッダー */}
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 20, marginBottom: collapsed ? 0 : 28 }}>
                <button onClick={() => toggleCat(cat.id, 'collapse')}
                  style={{ background: 'transparent', border: 'none', cursor: 'pointer', padding: 0,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    width: 64, height: 64, borderRadius: 4, flexShrink: 0,
                    background: cat.accent, color: '#fff',
                    fontSize: 32, fontFamily: '"JetBrains Mono", monospace', fontWeight: 700,
                    transform: collapsed ? 'rotate(-90deg)' : 'none', transition: 'transform .2s', }}>
                  {cat.glyph}
                </button>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, marginBottom: 6 }}>
                    <h2 style={{ fontSize: 32, fontWeight: 800, margin: 0, letterSpacing: -0.5 }}>{cat.name}</h2>
                    <span style={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 11, color: inkDim, letterSpacing: 1 }}>
                      {String(ci+1).padStart(2,'0')} / {data.categories.length} · {cat.nameEn}
                    </span>
                    <span style={{ marginLeft: 'auto', fontFamily: '"JetBrains Mono", monospace', fontSize: 10, color: inkDim, letterSpacing: 1 }}>
                      {cat.items.length} stories
                    </span>
                    <button onClick={() => toggleCat(cat.id, 'collapse')} style={{ border: 'none', background: 'transparent', color: inkDim, cursor: 'pointer', fontSize: 11, fontFamily: '"JetBrains Mono", monospace' }}>
                      {collapsed ? '[+] EXPAND' : '[−] COLLAPSE'}
                    </button>
                  </div>
                  <div style={{ fontSize: 15, color: inkDim, lineHeight: 1.6, fontStyle: 'italic' }}>{cat.summary}</div>
                </div>
              </div>

              {/* 3カラムグリッド */}
              {!collapsed && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '0 28px', paddingLeft: 84 }}>
                  {cat.items.map((it, idx) => {
                    const key = cat.id + '-' + idx;
                    const isExp = expandedNews.includes(key);
                    const isStar = starred.includes(key);
                    const isRead = read.includes(key);
                    return (
                      <article key={key}
                        style={{
                          padding: '16px 0', borderTop: idx < 2 ? 'none' : `1px solid ${border}`,
                          opacity: isRead ? 0.5 : 1, transition: 'opacity .2s',
                          gridColumn: idx === 0 ? '1 / -1' : 'auto',
                        }}>
                        <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
                          {/* スコア */}
                          <div style={{ flexShrink: 0, width: 56, textAlign: 'right' }}>
                            <div style={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 22, fontWeight: 700, color: cat.accent, lineHeight: 1 }}>
                              {it.score}
                            </div>
                            <div style={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 9, color: inkDim, letterSpacing: 1, marginTop: 2 }}>SCORE</div>
                          </div>

                          {/* サムネイル */}
                          <NewsThumb thumb={it.thumb} accent={cat.accent} size={idx === 0 ? 'lg' : 'md'} />

                          {/* 本文 */}
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 6, fontFamily: '"JetBrains Mono", monospace', fontSize: 10, color: inkDim, letterSpacing: 0.5 }}>
                              <span>{it.time}</span>
                              <span>·</span>
                              <span>{it.source}</span>
                              {idx === 0 && <span style={{ marginLeft: 'auto', background: cat.accent, color: '#fff', padding: '2px 6px', borderRadius: 2, fontWeight: 700 }}>TOP</span>}
                            </div>
                            <h3 onClick={() => toggleExpand(key)}
                              style={{
                                fontSize: idx === 0 ? 22 : 16, fontWeight: 700, margin: '0 0 8px',
                                lineHeight: 1.4, cursor: 'pointer',
                                textDecoration: isRead ? 'line-through' : 'none',
                                textDecorationColor: inkDim, textDecorationThickness: 1,
                              }}>
                              {it.title}
                            </h3>
                            <p style={{
                              fontSize: idx === 0 ? 14 : 13, lineHeight: 1.75, color: inkDim, margin: 0,
                              display: '-webkit-box', WebkitBoxOrient: 'vertical',
                              WebkitLineClamp: isExp ? 'none' : (idx === 0 ? 3 : 2),
                              overflow: 'hidden',
                            }}>{it.body}</p>

                            {/* アクション */}
                            <div style={{ display: 'flex', gap: 14, marginTop: 10, fontFamily: '"JetBrains Mono", monospace', fontSize: 10, letterSpacing: 1 }}>
                              <button onClick={() => toggleStar(key)} style={{ border: 'none', background: 'transparent', color: isStar ? cat.accent : inkDim, cursor: 'pointer', padding: 0, display: 'flex', alignItems: 'center', gap: 4 }}>
                                <span style={{ fontSize: 14 }}>{isStar ? '★' : '☆'}</span> {isStar ? 'STARRED' : 'STAR'}
                              </button>
                              <button onClick={() => toggleRead(key)} style={{ border: 'none', background: 'transparent', color: isRead ? cat.accent : inkDim, cursor: 'pointer', padding: 0 }}>
                                {isRead ? '◉ READ' : '○ MARK READ'}
                              </button>
                              <button onClick={() => toggleExpand(key)} style={{ border: 'none', background: 'transparent', color: inkDim, cursor: 'pointer', padding: 0 }}>
                                {isExp ? '− COLLAPSE' : '+ EXPAND'}
                              </button>
                            </div>
                          </div>
                        </div>
                      </article>
                    );
                  })}
                </div>
              )}
            </section>
          );
        })}
      </div>

      {/* ───── テーマ考察 ───── */}
      <div style={{ background: surface2, borderTop: `2px solid ${accent}`, padding: '48px 56px 56px' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '180px 1fr 240px', gap: 36, alignItems: 'flex-start' }}>
          <div>
            <div style={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 10, color: inkDim, letterSpacing: 2, marginBottom: 12 }}>EDITORIAL</div>
            <div style={{ fontSize: 60, fontFamily: '"JetBrains Mono", monospace', fontWeight: 700, color: accent, lineHeight: 1 }}>§</div>
            <div style={{ fontSize: 11, color: inkDim, marginTop: 10, fontFamily: '"JetBrains Mono", monospace' }}>by News Grasp Editor</div>
          </div>
          <div>
            <h2 style={{ fontSize: 30, fontWeight: 800, margin: '0 0 6px', letterSpacing: -0.5 }}>{data.reflection.title}</h2>
            <div style={{ fontSize: 15, color: inkDim, marginBottom: 24, fontStyle: 'italic' }}>{data.reflection.subtitle}</div>
            {data.reflection.body.map((p, i) => (
              <p key={i} style={{ fontSize: 15, lineHeight: 2, margin: '0 0 16px', textIndent: '1em' }}>{p}</p>
            ))}
          </div>
          <div style={{ borderLeft: `1px solid ${border}`, paddingLeft: 24 }}>
            <div style={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 10, color: inkDim, letterSpacing: 1.5, marginBottom: 14 }}>RELATED ISSUES</div>
            {data.reflection.related.map((r, i) => (
              <div key={i} style={{ marginBottom: 14, paddingBottom: 14, borderBottom: i < 2 ? `1px solid ${border}` : 'none' }}>
                <div style={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 10, color: inkDim }}>{r.date}</div>
                <div style={{ fontSize: 13, fontWeight: 600, marginTop: 4, lineHeight: 1.5 }}>{r.title}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ───── フッター ───── */}
      <div style={{ padding: '32px 56px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontFamily: '"JetBrains Mono", monospace', fontSize: 10, color: inkDim, letterSpacing: 1 }}>
        <div>NEWS GRASP · ISSUE #{data.issue.no} · {data.issue.date}</div>
        <div>UNSUBSCRIBE · ARCHIVE · OBSIDIAN.MD →</div>
      </div>
    </div>
  );
}

window.PCNewsletter = PCNewsletter;
