// News Grasp — メール送信可能 HTML newsletter
// Email-safe: table layout, inline CSS, no JS, no flexbox/grid
// マーカー: [[text]] = 太字, __text__ = 下線

function renderInlineEmphasis(text, accent) {
  // [[bold]] と __underline__ を <strong>, <span underline> に変換
  const parts = [];
  let i = 0;
  while (i < text.length) {
    if (text[i] === '[' && text[i+1] === '[') {
      const end = text.indexOf(']]', i+2);
      if (end !== -1) {
        parts.push(<strong key={i} style={{ fontWeight: 800, color: accent, background: accent+'18', padding: '0 3px', borderRadius: 2 }}>{text.slice(i+2, end)}</strong>);
        i = end + 2; continue;
      }
    }
    if (text[i] === '_' && text[i+1] === '_') {
      const end = text.indexOf('__', i+2);
      if (end !== -1) {
        parts.push(<span key={i} style={{ borderBottom: `2px solid ${accent}`, paddingBottom: 1, fontWeight: 700 }}>{text.slice(i+2, end)}</span>);
        i = end + 2; continue;
      }
    }
    let next = text.length;
    const b = text.indexOf('[[', i); if (b !== -1) next = Math.min(next, b);
    const u = text.indexOf('__', i); if (u !== -1) next = Math.min(next, u);
    parts.push(text.slice(i, next));
    i = next;
  }
  return parts;
}

function EmailNewsletter() {
  const data = window.NEWS_DATA_FULL;
  const ink = '#1A1A1A';
  const inkDim = '#5C5A52';
  const inkSoft = '#8B8B85';
  const border = '#E2DED4';
  const borderSoft = '#EDEAE3';
  const paper = '#FAF7F0';
  const headAccent = '#1A1A1A';
  const serif = '"Noto Serif JP", "Yu Mincho", "游明朝", "Hiragino Mincho ProN", serif';
  const mono = '"JetBrains Mono", "Menlo", monospace';

  return (
    <table cellPadding="0" cellSpacing="0" border="0" width="100%" style={{ background: '#E8E4DA', padding: '24px 0', fontFamily: serif }}>
      <tbody><tr><td align="center">
        <table cellPadding="0" cellSpacing="0" border="0" width="640" style={{ width: 640, maxWidth: '100%', background: paper, color: ink }}>
          <tbody>

          {/* ─── Masthead ─── */}
          <tr><td style={{ borderTop: `4px solid ${headAccent}`, padding: '32px 36px 20px' }}>
            <table cellPadding="0" cellSpacing="0" border="0" width="100%"><tbody><tr>
              <td style={{ verticalAlign: 'middle' }}>
                <table cellPadding="0" cellSpacing="0" border="0"><tbody><tr>
                  <td style={{ background: headAccent, color: paper, fontFamily: mono, fontSize: 13, fontWeight: 700, padding: '4px 8px', letterSpacing: 1 }}>NG</td>
                  <td style={{ paddingLeft: 10, fontFamily: mono, fontSize: 11, color: inkDim, letterSpacing: 1.5 }}>NEWS GRASP · {data.issue.edition}</td>
                </tr></tbody></table>
              </td>
              <td align="right" style={{ fontFamily: mono, fontSize: 11, color: inkDim }}>
                #{data.issue.no} · {data.issue.date}（{data.issue.weekday}）
              </td>
            </tr></tbody></table>
            <h1 style={{ fontSize: 34, fontWeight: 900, lineHeight: 1.25, letterSpacing: -0.5, margin: '18px 0 8px' }}>
              五つの視点で、<br />今日を掴む。
            </h1>
            <div style={{ fontFamily: mono, fontSize: 11, color: inkDim, letterSpacing: 1, marginTop: 6 }}>
              {data.categories.length} CATEGORIES · {data.categories.reduce((a,c)=>a+c.items.length,0)} STORIES · {data.reflection.sections.length} ESSAY SECTIONS
            </div>
          </td></tr>

          {/* ─── 目次 ─── */}
          <tr><td style={{ padding: '0 36px 24px' }}>
            <table cellPadding="0" cellSpacing="0" border="0" width="100%" style={{ background: '#F2EEE3', border: `1px solid ${border}` }}>
              <tbody><tr><td style={{ padding: '14px 18px' }}>
                <div style={{ fontFamily: mono, fontSize: 10, color: inkDim, letterSpacing: 2, marginBottom: 10 }}>┌── TABLE OF CONTENTS</div>
                {data.categories.map((c, i) => (
                  <table key={c.id} cellPadding="0" cellSpacing="0" border="0" width="100%" style={{ marginBottom: 4 }}><tbody><tr>
                    <td width="32" style={{ fontFamily: mono, fontSize: 12, color: c.accent, fontWeight: 700 }}>{String(i+1).padStart(2,'0')}.</td>
                    <td style={{ fontSize: 14, fontWeight: 700 }}>
                      <span style={{ color: c.accent, marginRight: 6, fontFamily: mono }}>{c.glyph}</span>
                      {c.name}
                      <span style={{ color: inkSoft, fontWeight: 400, fontSize: 11, fontFamily: mono, marginLeft: 8 }}>{c.nameEn}</span>
                    </td>
                    <td align="right" style={{ fontFamily: mono, fontSize: 11, color: inkDim }}>{c.items.length} items</td>
                  </tr></tbody></table>
                ))}
                <table cellPadding="0" cellSpacing="0" border="0" width="100%" style={{ marginTop: 8, paddingTop: 8, borderTop: `1px solid ${border}` }}><tbody><tr>
                  <td width="32" style={{ fontFamily: mono, fontSize: 12, color: ink, fontWeight: 700 }}>§.</td>
                  <td style={{ fontSize: 14, fontWeight: 700 }}>本日のテーマ考察</td>
                  <td align="right" style={{ fontFamily: mono, fontSize: 11, color: inkDim }}>{data.reflection.sections.length} sections</td>
                </tr></tbody></table>
              </td></tr></tbody>
            </table>
          </td></tr>

          {/* ─── 各カテゴリ ─── */}
          {data.categories.map((cat, ci) => (
            <React.Fragment key={cat.id}>
              {/* カテゴリヘッダー */}
              <tr><td style={{ background: cat.accent, padding: '20px 36px' }}>
                <table cellPadding="0" cellSpacing="0" border="0" width="100%"><tbody><tr>
                  <td style={{ verticalAlign: 'middle' }}>
                    <div style={{ fontFamily: mono, fontSize: 10, color: 'rgba(255,255,255,0.7)', letterSpacing: 2, marginBottom: 4 }}>
                      CATEGORY {String(ci+1).padStart(2,'0')} / {String(data.categories.length).padStart(2,'0')} · {cat.nameEn.toUpperCase()}
                    </div>
                    <div style={{ fontSize: 28, fontWeight: 800, color: '#fff', letterSpacing: -0.5, lineHeight: 1.1 }}>
                      <span style={{ fontFamily: mono, marginRight: 10 }}>{cat.glyph}</span>{cat.name}
                    </div>
                  </td>
                  <td align="right" style={{ verticalAlign: 'middle', color: 'rgba(255,255,255,0.85)', fontFamily: mono, fontSize: 11 }}>
                    {cat.items.length} stories
                  </td>
                </tr></tbody></table>
                <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.95)', fontStyle: 'italic', lineHeight: 1.6, marginTop: 10, paddingTop: 10, borderTop: '1px solid rgba(255,255,255,0.25)' }}>
                  {cat.summary}
                </div>
              </td></tr>

              {/* ニュース記事 */}
              {cat.items.map((it, idx) => (
                <tr key={idx}><td style={{ padding: '24px 36px', background: idx % 2 === 0 ? paper : '#F5F1E7', borderBottom: `1px solid ${borderSoft}` }}>
                  {/* メタ行 */}
                  <table cellPadding="0" cellSpacing="0" border="0" width="100%" style={{ marginBottom: 6 }}><tbody><tr>
                    <td>
                      <table cellPadding="0" cellSpacing="0" border="0"><tbody><tr>
                        <td style={{ background: cat.accent, color: '#fff', fontFamily: mono, fontSize: 11, fontWeight: 700, padding: '2px 6px', letterSpacing: 0.5 }}>
                          {String(idx+1).padStart(2,'0')}
                        </td>
                        <td style={{ paddingLeft: 8, fontFamily: mono, fontSize: 10, color: inkDim, letterSpacing: 0.5 }}>
                          {it.time} · {it.source}
                        </td>
                      </tr></tbody></table>
                    </td>
                    <td align="right" style={{ fontFamily: mono, fontSize: 10, color: inkDim }}>
                      SCORE <span style={{ color: cat.accent, fontWeight: 700, fontSize: 14, marginLeft: 4 }}>{it.score}</span>
                    </td>
                  </tr></tbody></table>

                  {/* タイトル */}
                  <h3 style={{ fontSize: idx === 0 ? 19 : 16, fontWeight: 800, lineHeight: 1.45, margin: '8px 0 12px', letterSpacing: -0.3 }}>
                    {idx === 0 && <span style={{ background: cat.accent, color: '#fff', fontFamily: mono, fontSize: 10, padding: '2px 6px', marginRight: 8, verticalAlign: 'middle', letterSpacing: 1 }}>TOP</span>}
                    {it.title}
                  </h3>

                  {/* TOP記事のみフルブリードサムネ */}
                  {idx === 0 && it.thumb && (
                    <div style={{ marginBottom: 14, position: 'relative', border: `1px solid ${border}` }}>
                      <img src={it.thumb} alt="" width="568" style={{ width: '100%', height: 200, objectFit: 'cover', display: 'block' }} />
                      <div style={{ position: 'absolute', bottom: 0, left: 0, background: cat.accent, color: '#fff', fontFamily: mono, fontSize: 10, padding: '4px 10px', letterSpacing: 1.5 }}>
                        FEATURED · {cat.nameEn.toUpperCase()}
                      </div>
                    </div>
                  )}

                  {/* サムネイル + 箇条書き（2件目以降は左にサムネ） */}
                  <table cellPadding="0" cellSpacing="0" border="0" width="100%"><tbody><tr>
                    {idx > 0 && it.thumb && (
                      <td width="140" valign="top" style={{ paddingRight: 16 }}>
                        <img src={it.thumb} alt="" width="140" style={{ width: 140, height: 90, objectFit: 'cover', display: 'block', border: `1px solid ${border}` }} />
                        <div style={{ fontFamily: mono, fontSize: 8, color: inkSoft, letterSpacing: 1, marginTop: 4, textAlign: 'center' }}>
                          ▢ IMG · {String(idx+1).padStart(2,'0')}
                        </div>
                      </td>
                    )}
                    <td valign="top">
                      <ul style={{ margin: 0, paddingLeft: 0, listStyle: 'none' }}>
                        {it.bullets.map((b, bi) => (
                          <li key={bi} style={{ position: 'relative', paddingLeft: 18, marginBottom: 8, fontSize: 13, lineHeight: 1.85, color: ink }}>
                            <span style={{ position: 'absolute', left: 0, top: 0, color: cat.accent, fontWeight: 700, fontFamily: mono }}>▸</span>
                            {renderInlineEmphasis(b, cat.accent)}
                          </li>
                        ))}
                      </ul>
                    </td>
                  </tr></tbody></table>
                </td></tr>
              ))}
            </React.Fragment>
          ))}

          {/* ─── テーマ考察 ヘッダー ─── */}
          <tr><td style={{ background: '#1A1A1A', padding: '40px 36px 32px', position: 'relative' }}>
            <table cellPadding="0" cellSpacing="0" border="0" width="100%"><tbody><tr>
              <td>
                <div style={{ fontFamily: mono, fontSize: 10, color: '#C9B98A', letterSpacing: 4, marginBottom: 12 }}>
                  ━━━━━━━━━━━━━━━━━━━━━━━<br/>
                  EDITORIAL · 本日のテーマ考察
                </div>
                <h2 style={{ fontSize: 36, fontWeight: 900, margin: '0 0 8px', letterSpacing: -1, color: '#FAF7F0', lineHeight: 1.15 }}>
                  {data.reflection.title}
                </h2>
                <div style={{ fontSize: 15, color: '#C9B98A', fontStyle: 'italic', fontFamily: mono, marginBottom: 24 }}>
                  {data.reflection.subtitle}
                </div>
              </td>
              <td width="120" align="right" valign="top">
                <div style={{ fontFamily: mono, fontSize: 64, color: '#3A3530', lineHeight: 1, fontWeight: 900 }}>§</div>
                <div style={{ fontFamily: mono, fontSize: 9, color: '#9C9A92', letterSpacing: 2, marginTop: 4 }}>{data.reflection.sections.length} PARTS</div>
              </td>
            </tr></tbody></table>

            {/* リード */}
            <div style={{ borderTop: '1px solid #3A3530', paddingTop: 20, marginTop: 8 }}>
              <div style={{ fontFamily: mono, fontSize: 10, color: '#C9B98A', letterSpacing: 2, marginBottom: 10 }}>┌── LEAD</div>
              <div style={{ fontSize: 15, lineHeight: 1.95, color: '#E8E2D4', paddingLeft: 12, borderLeft: '3px solid #C9B98A' }}>
                本日5分野・50本のニュースから浮かび上がる最大のテーマは、<strong style={{ color: '#fff', background: '#8E2A19', padding: '0 4px' }}>金利の天井</strong> と <strong style={{ color: '#fff', background: '#2D5BB8', padding: '0 4px' }}>AIの底入れ</strong> の同時進行である。以下、各カテゴリを横断して読み解く。
              </div>
            </div>
          </td></tr>

          {/* プルクオート */}
          <tr><td style={{ background: '#FAF7F0', padding: '28px 36px', borderTop: `1px solid ${border}`, borderBottom: `1px solid ${border}` }}>
            <table cellPadding="0" cellSpacing="0" border="0" width="100%"><tbody><tr>
              <td width="56" valign="top" style={{ fontFamily: 'Georgia, serif', fontSize: 80, color: '#8E2A19', lineHeight: 0.9, fontWeight: 900 }}>“</td>
              <td valign="middle" style={{ paddingLeft: 4 }}>
                <div style={{ fontSize: 19, fontWeight: 700, lineHeight: 1.6, color: ink, letterSpacing: -0.2 }}>
                  「単一の強い製品」から<br />
                  「<span style={{ borderBottom: '3px solid #8E2A19', paddingBottom: 2 }}>エコシステムでの占有率</span>」へ──<br />
                  プラットフォーム経済が<br />成熟期に入った日。
                </div>
                <div style={{ fontFamily: mono, fontSize: 10, color: inkDim, letterSpacing: 1.5, marginTop: 12 }}>
                  ─── PULL QUOTE · §06 GAME より
                </div>
              </td>
            </tr></tbody></table>
          </td></tr>

          {/* 各セクション */}
          {data.reflection.sections.map((sec, si) => {
            const isLast = si === data.reflection.sections.length - 1;
            const sectionTags = ['総論', '為替', 'AI', 'IT', '経済', 'ゲーム', '明日へ'];
            const sectionAccents = ['#1A1A1A', '#B8860B', '#2D5BB8', '#2E6B52', '#8E2A19', '#5E3D8C', '#C9B98A'];
            const accent = sectionAccents[si] || '#1A1A1A';
            return (
              <tr key={si}><td style={{ background: '#FAF7F0', padding: '0 36px' }}>
                <table cellPadding="0" cellSpacing="0" border="0" width="100%" style={{ borderBottom: isLast ? 'none' : `1px dashed ${border}` }}><tbody><tr>
                  {/* 番号カラム */}
                  <td width="80" valign="top" style={{ paddingTop: 28, paddingRight: 16, paddingBottom: 28 }}>
                    <div style={{
                      fontFamily: mono, fontSize: 38, fontWeight: 900,
                      color: accent, lineHeight: 0.9, letterSpacing: -2,
                    }}>§{String(si+1).padStart(2,'0')}</div>
                    <div style={{
                      fontFamily: mono, fontSize: 9, color: '#fff',
                      background: accent, padding: '2px 6px', display: 'inline-block',
                      letterSpacing: 1.5, marginTop: 8,
                    }}>{sectionTags[si] || ''}</div>
                  </td>
                  {/* 本文カラム */}
                  <td valign="top" style={{ paddingTop: 28, paddingBottom: 28, borderLeft: `1px solid ${border}`, paddingLeft: 20 }}>
                    <h3 style={{ fontSize: 18, fontWeight: 800, margin: '0 0 14px', color: ink, letterSpacing: -0.3, lineHeight: 1.4 }}>
                      {sec.heading}
                    </h3>
                    <div style={{ fontSize: 13.5, lineHeight: 2.0, color: ink, whiteSpace: 'pre-line' }}>
                      {renderInlineEmphasis(sec.body, accent)}
                    </div>
                  </td>
                </tr></tbody></table>
              </td></tr>
            );
          })}

          {/* キーテイクアウェイ */}
          <tr><td style={{ background: '#F2EEE3', padding: '28px 36px', borderTop: `2px solid ${ink}` }}>
            <div style={{ fontFamily: mono, fontSize: 10, color: inkDim, letterSpacing: 2, marginBottom: 16 }}>━━ KEY TAKEAWAYS · 今日の3つの結論</div>
            <table cellPadding="0" cellSpacing="0" border="0" width="100%"><tbody>
              {[
                { n: '01', tag: '為替', color: '#B8860B', text: '日米金利差の縮小は[[構造的]]。投機ではなく実需のポジション調整局面に入った。' },
                { n: '02', tag: 'AI', color: '#2D5BB8', text: 'PoCから本番への[[フェーズ転換]]。エージェント実需化と規制具体化が両輪で進む。' },
                { n: '03', tag: '産業', color: '#2E6B52', text: '案件性質による[[二極化]]──従来型SI+1.2% vs 生成AI特化型+15%、中位層に最大の転換圧力。' },
              ].map((t, i) => (
                <tr key={i}><td style={{ paddingBottom: 12 }}>
                  <table cellPadding="0" cellSpacing="0" border="0" width="100%" style={{ background: '#fff', border: `1px solid ${border}` }}><tbody><tr>
                    <td width="56" valign="middle" style={{ background: t.color, color: '#fff', textAlign: 'center', fontFamily: mono, fontSize: 18, fontWeight: 900, padding: '14px 0' }}>
                      {t.n}
                    </td>
                    <td style={{ padding: '12px 16px' }}>
                      <div style={{ fontFamily: mono, fontSize: 9, color: t.color, fontWeight: 700, letterSpacing: 1.5, marginBottom: 4 }}>
                        {t.tag.toUpperCase()}
                      </div>
                      <div style={{ fontSize: 13, lineHeight: 1.7, fontWeight: 600 }}>
                        {renderInlineEmphasis(t.text, t.color)}
                      </div>
                    </td>
                  </tr></tbody></table>
                </td></tr>
              ))}
            </tbody></table>
          </td></tr>

          {/* 関連過去号 */}
          <tr><td style={{ padding: '28px 36px', background: '#F2EEE3', borderTop: `2px solid ${border}` }}>
            <div style={{ fontFamily: mono, fontSize: 10, color: inkDim, letterSpacing: 2, marginBottom: 12 }}>━━ RELATED ISSUES · 関連する過去号</div>
            <table cellPadding="0" cellSpacing="0" border="0" width="100%"><tbody>
              {data.reflection.related.map((r, i) => (
                <tr key={i}><td style={{ padding: '10px 0', borderBottom: i < 2 ? `1px solid ${border}` : 'none' }}>
                  <table cellPadding="0" cellSpacing="0" border="0" width="100%"><tbody><tr>
                    <td width="100" style={{ fontFamily: mono, fontSize: 11, color: inkDim }}>{r.date}</td>
                    <td style={{ fontSize: 13, fontWeight: 600 }}>{r.title}</td>
                    <td width="20" align="right" style={{ color: inkDim }}>→</td>
                  </tr></tbody></table>
                </td></tr>
              ))}
            </tbody></table>
          </td></tr>

          {/* ─── フッター ─── */}
          <tr><td style={{ padding: '24px 36px 32px', background: paper, borderTop: `1px solid ${border}`, fontFamily: mono, fontSize: 10, color: inkDim, letterSpacing: 1, textAlign: 'center' }}>
            <div style={{ marginBottom: 8 }}>
              <strong style={{ color: ink, letterSpacing: 2 }}>NEWS GRASP</strong> — Five Lenses on Today
            </div>
            <div>ISSUE #{data.issue.no} · {data.issue.date} · MORNING EDITION</div>
            <div style={{ marginTop: 14, paddingTop: 14, borderTop: `1px solid ${border}` }}>
              <a href="#" style={{ color: inkDim, textDecoration: 'none', margin: '0 8px' }}>UNSUBSCRIBE</a>·
              <a href="#" style={{ color: inkDim, textDecoration: 'none', margin: '0 8px' }}>ARCHIVE</a>·
              <a href="#" style={{ color: inkDim, textDecoration: 'none', margin: '0 8px' }}>WEB VERSION</a>·
              <a href="#" style={{ color: inkDim, textDecoration: 'none', margin: '0 8px' }}>OBSIDIAN.MD</a>
            </div>
          </td></tr>

          </tbody>
        </table>
      </td></tr></tbody>
    </table>
  );
}

window.EmailNewsletter = EmailNewsletter;
window.renderInlineEmphasis = renderInlineEmphasis;
