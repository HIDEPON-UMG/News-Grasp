// News Grasp — モバイル用 メールHTML（375px幅、シングルカラム）

function EmailMobile() {
  const data = window.NEWS_DATA_FULL;
  const ink = '#1A1A1A';
  const inkDim = '#5C5A52';
  const inkSoft = '#8B8B85';
  const border = '#E2DED4';
  const borderSoft = '#EDEAE3';
  const paper = '#FAF7F0';
  const headAccent = '#1A1A1A';
  const serif = '"Noto Serif JP", "Yu Mincho", serif';
  const mono = '"JetBrains Mono", monospace';
  const renderE = window.renderInlineEmphasis;

  return (
    <table cellPadding="0" cellSpacing="0" border="0" width="100%" style={{ background: '#E8E4DA', fontFamily: serif }}>
      <tbody><tr><td>
        <table cellPadding="0" cellSpacing="0" border="0" width="100%" style={{ background: paper, color: ink }}>
          <tbody>

          <tr><td style={{ borderTop: `4px solid ${headAccent}`, padding: '20px 18px 16px' }}>
            <table cellPadding="0" cellSpacing="0" border="0"><tbody><tr>
              <td style={{ background: headAccent, color: paper, fontFamily: mono, fontSize: 11, fontWeight: 700, padding: '3px 6px' }}>NG</td>
              <td style={{ paddingLeft: 8, fontFamily: mono, fontSize: 10, color: inkDim, letterSpacing: 1.2 }}>NEWS GRASP · #{data.issue.no}</td>
            </tr></tbody></table>
            <h1 style={{ fontSize: 22, fontWeight: 900, lineHeight: 1.25, margin: '12px 0 4px' }}>
              五つの視点で、<br />今日を掴む。
            </h1>
            <div style={{ fontFamily: mono, fontSize: 10, color: inkDim, marginTop: 4 }}>
              {data.issue.date}（{data.issue.weekday}）
            </div>
          </td></tr>

          <tr><td style={{ padding: '0 18px 16px' }}>
            <table cellPadding="0" cellSpacing="0" border="0" width="100%" style={{ background: '#F2EEE3', border: `1px solid ${border}` }}>
              <tbody><tr><td style={{ padding: '10px 12px' }}>
                <div style={{ fontFamily: mono, fontSize: 9, color: inkDim, letterSpacing: 1.5, marginBottom: 6 }}>┌── INDEX</div>
                {data.categories.map((c, i) => (
                  <div key={c.id} style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>
                    <span style={{ fontFamily: mono, color: c.accent, marginRight: 6 }}>{String(i+1).padStart(2,'0')}.{c.glyph}</span>
                    {c.name} <span style={{ color: inkSoft, fontWeight: 400, fontFamily: mono, fontSize: 10 }}>· {c.items.length}</span>
                  </div>
                ))}
                <div style={{ fontSize: 12, fontWeight: 700, marginTop: 6, paddingTop: 6, borderTop: `1px solid ${border}` }}>
                  <span style={{ fontFamily: mono, marginRight: 6 }}>§.</span>本日のテーマ考察
                </div>
              </td></tr></tbody>
            </table>
          </td></tr>

          {data.categories.map((cat, ci) => (
            <React.Fragment key={cat.id}>
              <tr><td style={{ background: cat.accent, padding: '14px 18px' }}>
                <div style={{ fontFamily: mono, fontSize: 9, color: 'rgba(255,255,255,0.75)', letterSpacing: 2, marginBottom: 2 }}>
                  {String(ci+1).padStart(2,'0')} / {String(data.categories.length).padStart(2,'0')} · {cat.nameEn.toUpperCase()}
                </div>
                <div style={{ fontSize: 22, fontWeight: 800, color: '#fff', letterSpacing: -0.3 }}>
                  <span style={{ fontFamily: mono, marginRight: 8 }}>{cat.glyph}</span>{cat.name}
                </div>
                <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.95)', fontStyle: 'italic', lineHeight: 1.6, marginTop: 6, paddingTop: 6, borderTop: '1px solid rgba(255,255,255,0.25)' }}>
                  {cat.summary}
                </div>
              </td></tr>

              {cat.items.map((it, idx) => (
                <tr key={idx}><td style={{ padding: '14px 18px', background: idx % 2 === 0 ? paper : '#F5F1E7', borderBottom: `1px solid ${borderSoft}` }}>
                  <table cellPadding="0" cellSpacing="0" border="0" width="100%"><tbody><tr>
                    <td>
                      <span style={{ background: cat.accent, color: '#fff', fontFamily: mono, fontSize: 9, fontWeight: 700, padding: '1px 5px' }}>
                        {String(idx+1).padStart(2,'0')}
                      </span>
                      <span style={{ marginLeft: 6, fontFamily: mono, fontSize: 9, color: inkDim }}>{it.time}·{it.source}</span>
                    </td>
                    <td align="right" style={{ fontFamily: mono, fontSize: 9, color: inkDim }}>
                      SCR <span style={{ color: cat.accent, fontWeight: 700, fontSize: 12, marginLeft: 2 }}>{it.score}</span>
                    </td>
                  </tr></tbody></table>
                  <h3 style={{ fontSize: 14, fontWeight: 800, lineHeight: 1.4, margin: '6px 0 8px' }}>
                    {idx === 0 && <span style={{ background: cat.accent, color: '#fff', fontFamily: mono, fontSize: 9, padding: '1px 5px', marginRight: 6, verticalAlign: 'middle' }}>TOP</span>}
                    {it.title}
                  </h3>
                  {it.thumb && <img src={it.thumb} alt="" width="100%" style={{ width: '100%', height: 'auto', maxHeight: 180, objectFit: 'cover', display: 'block', marginBottom: 8, border: `1px solid ${border}` }} />}
                  <ul style={{ margin: 0, paddingLeft: 0, listStyle: 'none' }}>
                    {it.bullets.map((b, bi) => (
                      <li key={bi} style={{ position: 'relative', paddingLeft: 14, marginBottom: 6, fontSize: 12, lineHeight: 1.8 }}>
                        <span style={{ position: 'absolute', left: 0, color: cat.accent, fontWeight: 700, fontFamily: mono }}>▸</span>
                        {renderE(b, cat.accent)}
                      </li>
                    ))}
                  </ul>
                </td></tr>
              ))}
            </React.Fragment>
          ))}

          <tr><td style={{ background: '#1A1A1A', color: '#F5F1E7', padding: '24px 18px 18px' }}>
            <div style={{ fontFamily: mono, fontSize: 9, color: '#C9B98A', letterSpacing: 3, marginBottom: 8 }}>
              ━━━━━━━━━━━━<br/>EDITORIAL · 本日のテーマ考察
            </div>
            <h2 style={{ fontSize: 24, fontWeight: 900, margin: '0 0 4px', color: '#FAF7F0', letterSpacing: -0.5, lineHeight: 1.2 }}>{data.reflection.title}</h2>
            <div style={{ fontSize: 11, color: '#C9B98A', fontStyle: 'italic', fontFamily: mono, marginBottom: 14 }}>{data.reflection.subtitle}</div>
            <div style={{ borderTop: '1px solid #3A3530', paddingTop: 12, fontSize: 12, lineHeight: 1.85, color: '#E8E2D4', borderLeft: '3px solid #C9B98A', paddingLeft: 10 }}>
              本日5分野・50本のニュースから見える最大のテーマは、<strong style={{ color: '#fff', background: '#8E2A19', padding: '0 3px' }}>金利の天井</strong>と<strong style={{ color: '#fff', background: '#2D5BB8', padding: '0 3px' }}>AIの底入れ</strong>の同時進行。
            </div>
          </td></tr>

          {/* プルクオート */}
          <tr><td style={{ background: paper, padding: '18px', borderBottom: `1px solid ${border}` }}>
            <div style={{ fontFamily: 'Georgia, serif', fontSize: 48, color: '#8E2A19', lineHeight: 0.5, marginBottom: 4, fontWeight: 900 }}>“</div>
            <div style={{ fontSize: 15, fontWeight: 700, lineHeight: 1.55, letterSpacing: -0.2 }}>
              「単一の強い製品」から<br />「<span style={{ borderBottom: '2px solid #8E2A19' }}>エコシステムでの占有率</span>」へ。
            </div>
            <div style={{ fontFamily: mono, fontSize: 9, color: inkDim, letterSpacing: 1.2, marginTop: 8 }}>─── §06 GAME より</div>
          </td></tr>

          {data.reflection.sections.map((sec, si) => {
            const sectionTags = ['総論', '為替', 'AI', 'IT', '経済', 'ゲーム', '明日へ'];
            const sectionAccents = ['#1A1A1A', '#B8860B', '#2D5BB8', '#2E6B52', '#8E2A19', '#5E3D8C', '#C9B98A'];
            const accent = sectionAccents[si] || '#1A1A1A';
            return (
              <tr key={si}><td style={{ background: paper, padding: '16px 18px', borderBottom: `1px dashed ${border}` }}>
                <table cellPadding="0" cellSpacing="0" border="0"><tbody><tr>
                  <td style={{ fontFamily: mono, fontSize: 22, fontWeight: 900, color: accent, lineHeight: 1, paddingRight: 10 }}>§{String(si+1).padStart(2,'0')}</td>
                  <td style={{ verticalAlign: 'middle' }}>
                    <span style={{ fontFamily: mono, fontSize: 9, color: '#fff', background: accent, padding: '2px 5px', letterSpacing: 1.2 }}>{sectionTags[si] || ''}</span>
                  </td>
                </tr></tbody></table>
                <h3 style={{ fontSize: 14, fontWeight: 800, margin: '8px 0 8px', lineHeight: 1.4 }}>
                  {sec.heading}
                </h3>
                <div style={{ fontSize: 12.5, lineHeight: 1.95, whiteSpace: 'pre-line' }}>
                  {renderE(sec.body, accent)}
                </div>
            </td></tr>
          );
          })}

          <tr><td style={{ padding: '20px 18px 28px', textAlign: 'center', borderTop: `1px solid ${border}`, fontFamily: mono, fontSize: 9, color: inkDim, letterSpacing: 1 }}>
            <strong style={{ color: ink, letterSpacing: 2 }}>NEWS GRASP</strong><br />
            <div style={{ marginTop: 4 }}>#{data.issue.no} · {data.issue.date}</div>
          </td></tr>

          </tbody>
        </table>
      </td></tr></tbody>
    </table>
  );
}

window.EmailMobile = EmailMobile;
