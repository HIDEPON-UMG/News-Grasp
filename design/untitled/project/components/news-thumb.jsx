// News Grasp — 記事サムネイル
// thumb がある場合は <img> 表示、ない場合は SVG プレースホルダ（カテゴリアクセント＋NGロゴ）

function NewsThumb({ thumb, accent, size = 'md' }) {
  const dims = {
    sm: { w: 56, h: 56, logo: 14, sub: 7 },
    md: { w: 96, h: 72, logo: 18, sub: 8 },
    lg: { w: 200, h: 120, logo: 28, sub: 10 },
    xl: { w: 280, h: 160, logo: 36, sub: 11 },
  }[size];

  if (thumb) {
    return (
      <div style={{
        width: dims.w, height: dims.h, flexShrink: 0,
        borderRadius: 4, overflow: 'hidden', position: 'relative',
        background: '#222',
      }}>
        <img src={thumb} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
      </div>
    );
  }

  // プレースホルダ：斜めストライプ + News Grasp ロゴ
  const stripeId = 'ng-stripe-' + accent.replace('#', '');
  return (
    <div style={{
      width: dims.w, height: dims.h, flexShrink: 0,
      borderRadius: 4, overflow: 'hidden', position: 'relative',
      background: accent,
    }}>
      <svg width="100%" height="100%" viewBox={`0 0 ${dims.w} ${dims.h}`} preserveAspectRatio="none" style={{ position: 'absolute', inset: 0 }}>
        <defs>
          <pattern id={stripeId} patternUnits="userSpaceOnUse" width="14" height="14" patternTransform="rotate(45)">
            <rect width="14" height="14" fill={accent}/>
            <rect width="7" height="14" fill="rgba(255,255,255,0.08)"/>
          </pattern>
          <radialGradient id={stripeId+'-g'} cx="50%" cy="50%" r="60%">
            <stop offset="0%" stopColor="rgba(0,0,0,0)" />
            <stop offset="100%" stopColor="rgba(0,0,0,0.35)" />
          </radialGradient>
        </defs>
        <rect width={dims.w} height={dims.h} fill={`url(#${stripeId})`} />
        <rect width={dims.w} height={dims.h} fill={`url(#${stripeId}-g)`} />
      </svg>
      <div style={{
        position: 'absolute', inset: 0,
        display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center', gap: 2,
        color: '#fff', fontFamily: '"JetBrains Mono", monospace',
        textShadow: '0 1px 4px rgba(0,0,0,0.3)',
      }}>
        <div style={{
          fontSize: dims.logo, fontWeight: 800, letterSpacing: -0.5, lineHeight: 1,
          display: 'flex', alignItems: 'center', gap: 4,
        }}>
          <span style={{
            border: '1.5px solid #fff', padding: `1px ${dims.logo*0.25}px`,
            borderRadius: 2,
          }}>NG</span>
          {size !== 'sm' && <span>News Grasp</span>}
        </div>
        {size !== 'sm' && (
          <div style={{ fontSize: dims.sub, letterSpacing: 2, opacity: 0.85, marginTop: 2 }}>
            FIVE LENSES · ON TODAY
          </div>
        )}
      </div>
    </div>
  );
}

window.NewsThumb = NewsThumb;
