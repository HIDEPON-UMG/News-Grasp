---
title: "DeepDive解説対談: AI推論半導体、内製と複数調達へ"
date: "2026-08-19"
source: "digest/DeepDive/2026-08-19-DeepDive.md"
source_sha256: "5274ba00f3e4fe91eeebd74e520ae1ba4ee4a79d7e2c2655050898ee16cecff6"
type: "deepdive-dialogue"
audio_target_minutes: 6
context_sources:
  - date: "2026-08-11"
    title: "TSMC最高売上、AI半導体は工場運用戦へ"
    relation: "続報"
  - date: "2026-07-28"
    title: "AI半導体、設計と製造の一体契約へ"
    relation: "主役共有"
roles:
  senior:
    model_uuid: "47e53151-a378-46f3-abee-ce13aa07feb1"
  junior:
    model_uuid: "59f96896-64d2-4378-830a-4d5feb3d81aa"
---

## 台本

<!-- value:current_signal evidence:source:0 support:source:1 -->

若手: TSMCとSamsungの比較から、Teslaが製造網へ踏み込んだ今回の変化をどう見ますか。

先輩: 前回はTSMCとSamsungの先端量産力を比べた。今回の変化点は、Teslaが推論チップの設計だけでなく製造網にも踏み込み、Intelを戦略パートナーに加えたことだ。工場を選ぶ話から、顧客自身が設計、製造、包装の流れを組み替える話へ重心が移った。

若手: Teslaの内製構想を、既存の先端量産をすぐ置き換える計画とは読まない方がよいのですね。

<!-- value:evidence evidence:source:2 support:source:3 -->

若手: 顧客が供給網を設計する競争へ移った根拠は、どこにありますか。

先輩: 競争軸は工場の性能から、顧客が供給網をどこまで設計できるかへ移った。Teslaの構想は、TSMCをすぐ置き換える話ではない。内製と委託を工程ごとに組み合わせ、設計反復や供給交渉の選択肢を増やす方向だと整理できる。

若手: 工場の優劣だけでなく、委託と内製を工程別に組み合わせる力を比べるのですね。

<!-- value:causal_chain evidence:source:4 support:source:5 -->

若手: Teslaの投資とTSMCの規模が、供給再編へつながる経路を説明してください。

先輩: 同社は2026年の設備投資を200億ドル超とし、AI計算基盤、データセンター、製造・研究開発ラインへ振り向ける。一方、TSMCは2025年に534顧客・1万2682製品を支えた。Teslaの投資は供給の選択肢を増やすが、TSMCが持つ顧客網と製品運用の厚みを短期間で置き換えるものではない。

若手: 投資額の大きさだけで判断せず、TSMCの顧客数と製品運用を越えるまでの時間も見積もるのですね。

<!-- value:counterevidence_or_limit evidence:source:6 support:source:7 -->

若手: 内製化を進めれば依存はなくなる、と断定できない理由は何ですか。

先輩: 内製は依存を消す手段というより、供給交渉と設計反復を速める補完線になる。IntelとSamsungElectronicsは、同じ対抗陣営でも武器が異なる。Teslaが製造へ踏み込んでも、Intelの量産認定、Samsungのメモリと包装など不足する機能は別の経路で補う必要がある。

若手: 内製の効果を依存ゼロではなく、交渉力と設計反復の速さとして測るのですね。

<!-- value:change_over_time evidence:source:8 support:source:9 -->

若手: IntelとSamsungの動きから、競争の単位はどう変わりましたか。

先輩: IntelはTerafabへの製造・包装能力の提供で大口需要へ接近し、SamsungはBroadcomに2nm以下、HBM、先端包装を一体で示した。TSMC対抗の勝負は単一工程の価格ではなく、設計から包装までの束を誰が安定運用できるかにある。製造、メモリ、包装を分けて契約するだけでは、工程間の品質責任が途切れる可能性がある。

若手: 単価ではなく、設計から包装までの品質と責任を一体で動かせるかが変化の軸なのですね。

<!-- value:decision_implication evidence:source:10 support:source:11 -->

若手: TSMCの実績を顧客の調達判断へ持ち込むとき、何を条件分けしますか。

先輩: TSMCのQ2売上は前年同期の9337.92億台湾ドルから1兆2703.81億台湾ドルへ36.0%増え、前四半期比でも12.0%伸びた。しかも7nm以下がウエハー売上の77%を占める。先端量産の主軸としての実績は強いが、第二供給源の認定や工程横断の代替性は別の条件として確認する必要がある。

若手: 売上と7nm以下の比率は主軸の信頼性に使い、代替供給源は認定結果で別に判断するのですね。

<!-- value:next_action evidence:source:12 support:source:13 -->

若手: 次の調達会議までに、Tesla、Intel、TSMCの役割を誰がどう確認しますか。

先輩: 顧客が第二供給源を持っても、先端量産の主軸をすぐ外せる状況ではない。ただしIntelは、Q1のFoundry売上を54億ドル、前年同期比16%増まで伸ばし、Terafabへ戦略参加した。調達責任者は9月末までに設計、先端量産、HBM、包装、品質保証の供給源を工程別の二重化表にし、製造責任者はIntel Terafabの量産認定条件を検証記録へまとめる。14Aまたは先端包装の認定が期限までに得られなければ、CFOはTSMCの主軸契約を維持し、投資委員会でTesla内製の追加投資を止める。

若手: 調達責任者が二重化表、製造責任者が認定記録を9月末までに作り、認定が遅れればCFOが主軸を維持して追加投資を止める流れで進めてよいでしょうか。
