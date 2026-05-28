// News Grasp スマホメルマガ（モバイル版）
// 1カラム＋カテゴリタブ＋スクロール

function MobileNewsletter({ dark, expandedNews, toggleExpand, starred, toggleStar, read, toggleRead, activeCat, setActiveCat }) {
  const data = window.NEWS_DATA;
  const [showSummary, setShowSummary] = React.useState(false);

  const bg = dark ? '#0B0E1A' : '#F5F2EC';
  const surface = dark ? '#11162B' : '#FFFEFB';
  const ink = dark ? '#E8E5DD' : '#11131A';
  const inkDim = dark ? '#9C9A92' : '#6C6A62';
  const border = dark ? 'rgba(255,255,255,0.08)' : 'rgba(20,20,20,0.08)';

  const cat = data.categories.find(c => c.id === activeCat) || data.categories[0];
  const accent = cat.accent;

  return (
    <div style={{ background: bg, height: '100%', color: ink, fontFamily: '"Noto Serif JP", "Yu Mincho", serif', display: 'flex', flexDirection: 'column' }}>
      {/* ヘッダー */}
      <div style={{ padding: '12px 18px 10px', borderBottom: `1px solid ${border}`, flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <div style={{ width: 22, height: 22, borderRadius: 3, background: accent, color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: '"JetBrains Mono", monospace', fontSize: 11, fontWeight: 700 }}>NG</div>
          <div style={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 9, letterSpacing: 1.5, color: inkDim }}>NEWS GRASP · #{data.issue.no}</div>
          <div style={{ marginLeft: 'auto', fontFamily: '"JetBrains Mono", monospace', fontSize: 9, color: inkDim }}>{data.issue.date}</div>
        </div>
        <div style={{ fontSize: 22, fontWeight: 800, lineHeight: 1.2, letterSpacing: -0.3 }}>
          五つの視点で、今日を掴む。
        </div>
      </div>

      {/* カテゴリタブ */}
      <div style={{ display: 'flex', overflowX: 'auto', borderBottom: `1px solid ${border}`, flexShrink: 0, background: surface }}>
        {data.categories.map(c => (
          <button key={c.id} onClick={() => { setActiveCat(c.id); setShowSummary(false); }}
            style={{
              flex: '0 0 auto', padding: '12px 14px', border: 'none', background: 'transparent',
              borderBottom: activeCat === c.id && !showSummary ? `2px solid ${c.accent}` : '2px solid transparent',
              color: activeCat === c.id && !showSummary ? c.accent : inkDim,
              fontFamily: '"Noto Serif JP", serif', fontSize: 13, fontWeight: 700, cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: 6,
            }}>
            <span style={{ fontFamily: '"JetBrains Mono", monospace' }}>{c.glyph}</span>
            {c.name}
          </button>
        ))}
        <button onClick={() => setShowSummary(true)}
          style={{
            flex: '0 0 auto', padding: '12px 14px', border: 'none', background: 'transparent',
            borderBottom: showSummary ? `2px solid ${ink}` : '2px solid transparent',
            color: showSummary ? ink : inkDim,
            fontFamily: '"Noto Serif JP", serif', fontSize: 13, fontWeight: 700, cursor: 'pointer',
          }}>§ 考察</button>
      </div>

      {/* スクロール本体 */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {showSummary ? (
          <div style={{ padding: '20px 18px 32px' }}>
            <div style={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 9, color: inkDim, letterSpacing: 2, marginBottom: 8 }}>EDITORIAL</div>
            <h2 style={{ fontSize: 22, fontWeight: 800, margin: '0 0 4px', letterSpacing: -0.3 }}>{data.reflection.title}</h2>
            <div style={{ fontSize: 12, color: inkDim, marginBottom: 18, fontStyle: 'italic' }}>{data.reflection.subtitle}</div>
            {data.reflection.body.map((p, i) => (
              <p key={i} style={{ fontSize: 13, lineHeight: 1.95, margin: '0 0 14px', textIndent: '1em' }}>{p}</p>
            ))}
            <div style={{ marginTop: 24, paddingTop: 16, borderTop: `1px solid ${border}` }}>
              <div style={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 9, color: inkDim, letterSpacing: 1.5, marginBottom: 10 }}>RELATED</div>
              {data.reflection.related.map((r, i) => (
                <div key={i} style={{ padding: '10px 0', borderTop: i > 0 ? `1px solid ${border}` : 'none' }}>
                  <div style={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 9, color: inkDim }}>{r.date}</div>
                  <div style={{ fontSize: 12, fontWeight: 600, marginTop: 2 }}>{r.title}</div>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div>
            {/* カテゴリヘッダー */}
            <div style={{ padding: '18px 18px 12px', background: surface }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                <div style={{ width: 36, height: 36, borderRadius: 4, background: accent, color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: '"JetBrains Mono", monospace', fontWeight: 700, fontSize: 18 }}>
                  {cat.glyph}
                </div>
                <div>
                  <div style={{ fontSize: 18, fontWeight: 800, lineHeight: 1.1 }}>{cat.name}</div>
                  <div style={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 9, color: inkDim, letterSpacing: 1, marginTop: 2 }}>
                    {cat.nameEn} · {cat.items.length} stories
                  </div>
                </div>
              </div>
              <div style={{ fontSize: 12, color: inkDim, lineHeight: 1.5, fontStyle: 'italic', marginTop: 8 }}>{cat.summary}</div>
            </div>

            {/* ニュースリスト */}
            <div style={{ padding: '0 18px' }}>
              {cat.items.map((it, idx) => {
                const key = cat.id + '-' + idx;
                const isExp = expandedNews.includes(key);
                const isStar = starred.includes(key);
                const isRead = read.includes(key);
                return (
                  <article key={key} style={{ padding: '16px 0', borderTop: `1px solid ${border}`, opacity: isRead ? 0.5 : 1 }}>
                    <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', marginBottom: 6 }}>
                      <div style={{ flexShrink: 0, width: 36, textAlign: 'right', paddingTop: 1 }}>
                        <div style={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 16, fontWeight: 700, color: accent, lineHeight: 1 }}>{it.score}</div>
                        <div style={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 8, color: inkDim, letterSpacing: 1 }}>SCR</div>
                      </div>
                      <NewsThumb thumb={it.thumb} accent={accent} size="sm" />
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 9, color: inkDim, letterSpacing: 0.5, marginBottom: 4 }}>
                          {it.time} · {it.source}
                        </div>
                        <h3 onClick={() => toggleExpand(key)} style={{ fontSize: 14, fontWeight: 700, margin: '0 0 6px', lineHeight: 1.4, cursor: 'pointer' }}>
                          {idx === 0 && <span style={{ background: accent, color: '#fff', padding: '1px 5px', borderRadius: 2, fontFamily: '"JetBrains Mono", monospace', fontSize: 9, marginRight: 6, verticalAlign: 'middle', fontWeight: 700 }}>TOP</span>}
                          {it.title}
                        </h3>
                        <p style={{
                          fontSize: 12, lineHeight: 1.7, color: inkDim, margin: 0,
                          display: '-webkit-box', WebkitBoxOrient: 'vertical',
                          WebkitLineClamp: isExp ? 'none' : 2, overflow: 'hidden',
                        }}>{it.body}</p>
                        <div style={{ display: 'flex', gap: 14, marginTop: 8, fontFamily: '"JetBrains Mono", monospace', fontSize: 9, letterSpacing: 1 }}>
                          <button onClick={() => toggleStar(key)} style={{ border: 'none', background: 'transparent', color: isStar ? accent : inkDim, cursor: 'pointer', padding: 0, display: 'flex', alignItems: 'center', gap: 3 }}>
                            <span style={{ fontSize: 12 }}>{isStar ? '★' : '☆'}</span>{isStar ? 'STARRED' : 'STAR'}
                          </button>
                          <button onClick={() => toggleRead(key)} style={{ border: 'none', background: 'transparent', color: isRead ? accent : inkDim, cursor: 'pointer', padding: 0 }}>
                            {isRead ? '◉ READ' : '○ READ'}
                          </button>
                          <button onClick={() => toggleExpand(key)} style={{ border: 'none', background: 'transparent', color: inkDim, cursor: 'pointer', padding: 0 }}>
                            {isExp ? '−' : '+'}
                          </button>
                        </div>
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

window.MobileNewsletter = MobileNewsletter;
