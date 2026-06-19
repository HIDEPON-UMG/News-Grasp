from pathlib import Path

from tools.validate_incident_report_design import validate_report


def _write_reference_like_report(path: Path) -> None:
    long_body = "公開前 gate の停止理由、復旧作業、公開確認、次回の観測点を同じ紙面で説明する。" * 700
    path.write_text(
        f"""
<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700;900&family=Noto+Serif+JP:wght@500;600;700;900&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>
body{{background:#E6E1D5;color:#3A382F}}
code{{background:#F1EBDC;word-break:break-all;overflow-wrap:anywhere}}
</style>
</head>
<body>
<div style="background:#181C2A;color:#C9A155">News-Grasp · Incident Report</div>
<div style="background:#FFFFFF">
<h1 style="font-family:'Noto Serif JP',serif;font-size:44px;color:#181C2A">参照品質の障害報告</h1>
<div style="border-left:3px solid #C9A155">結論</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr)">
<div>Started</div><div style="background:#FCF2F0;color:#B83A2D">Stopped</div><div style="background:#F1F6F2;color:#3D7E60">Recovered</div><div>Published</div>
</div>
<section>
<h2>どの工程で問題が起きたか</h2>
<div>Workflow Map ✓ ✕ 未到達</div>
<div style="box-shadow:0 0 0 4px rgba(184,58,45,.1)">STOP</div>
<div style="border:1.5px dashed #B0AAA0">Fault boundary</div>
</section>
<section>
<h2>問題の詳細と、なぜ起きたか</h2>
<dl>
<div style="display:grid;grid-template-columns:200px 1fr;gap:24px;padding:18px 0"><dt>直接原因</dt><dd><strong style="font-weight:900;color:#181C2A">停止理由</strong></dd></div>
<div style="display:grid;grid-template-columns:200px 1fr;gap:24px;padding:18px 0"><dt>なぜ起きたか</dt><dd><span style="text-decoration:underline;text-decoration-color:#C9A155">判断軸</span></dd></div>
<div style="display:grid;grid-template-columns:200px 1fr;gap:24px;padding:18px 0"><dt>停止が公開停止になった理由</dt><dd><span style="background:#F6E7C6">News-Grasp</span></dd></div>
<div style="display:grid;grid-template-columns:200px 1fr;gap:24px;padding:18px 0"><dt>repair が救えなかった理由</dt><dd>二次要因</dd></div>
</dl>
<div>二次的に見つかった問題</div>
</section>
<section>
<h2>問題の暫定対応内容</h2>
<div style="position:relative;padding-left:30px">
<div style="position:absolute;left:5px;top:8px;bottom:8px;width:2px;background:#E1DCCF"></div>
<div style="box-shadow:0 0 0 2px #B83A2D">06:00</div>
<div style="box-shadow:0 0 0 2px #B07A39">06:10</div>
<div style="box-shadow:0 0 0 2px #3D7E60">06:20</div>
<div style="box-shadow:0 0 0 2px #3D7E60">06:30</div>
</div>
<div style="display:grid;grid-template-columns:1fr 1fr"><div>暫定修正</div><div>契約テスト</div></div>
</section>
<section><h2>直近改修・過去障害との関係</h2></section>
<section><h2>恒久対応方針の網羅性と完璧性の担保</h2><div>#FCF2F0 #F1F6F2 #3D7E60 #B83A2D #181C2A #C9A155</div></section>
<section><h2>恒久対応の実行計画</h2></section>
<p>{long_body}</p>
</div>
</body>
</html>
""",
        encoding="utf-8",
    )


def test_incident_report_design_rejects_class_based_report(tmp_path: Path) -> None:
    report = tmp_path / "incident.html"
    report.write_text(
        """
<!doctype html>
<html lang="ja">
<head><meta charset="utf-8"><style>.card{color:red}</style></head>
<body>
  <div class="card">結論</div>
  <section><h2>どの工程で問題が起きたか</h2></section>
</body>
</html>
""",
        encoding="utf-8",
    )

    result = validate_report(report)

    assert not result.ok
    assert any("class 属性" in error for error in result.errors)


def test_incident_report_design_rejects_thin_report_with_only_required_words(tmp_path: Path) -> None:
    report = tmp_path / "thin-incident.html"
    report.write_text(
        """
<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400&family=Noto+Serif+JP:wght@900&family=JetBrains+Mono:wght@700&display=swap" rel="stylesheet">
<style>
body{background:#E6E1D5;color:#3A382F}
code{background:#F1EBDC;word-break:break-all;overflow-wrap:anywhere}
</style>
</head>
<body>
<div style="background:#181C2A;color:#C9A155">News-Grasp · Incident Report</div>
<div style="background:#FFFFFF">
<h1 style="font-family:'Noto Serif JP',serif;font-size:44px;color:#181C2A">薄い障害報告</h1>
<div style="border-left:3px solid #C9A155">結論</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr)">
<div>Started</div><div style="background:#FCF2F0;color:#B83A2D">Stopped</div><div style="background:#F1F6F2;color:#3D7E60">Recovered</div><div>Published</div>
</div>
<section>どの工程で問題が起きたか STOP Fault boundary</section>
<section>問題の詳細と、なぜ起きたか</section>
<section>問題の暫定対応内容</section>
<section>直近改修・過去障害との関係</section>
<section>恒久対応方針の網羅性と完璧性の担保</section>
<section>恒久対応の実行計画</section>
</div>
</body>
</html>
""",
        encoding="utf-8",
    )

    result = validate_report(report)

    assert not result.ok
    assert any("工程図" in error for error in result.errors)
    assert any("紙面密度" in error for error in result.errors)


def test_reference_like_incident_report_follows_required_design(tmp_path: Path) -> None:
    report = tmp_path / "reference-like-report.html"
    _write_reference_like_report(report)
    result = validate_report(report)

    assert result.ok, result.errors
