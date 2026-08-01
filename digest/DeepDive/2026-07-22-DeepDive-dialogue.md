---
title: "DeepDive解説対談: Gemini Flash、役割分担が選定軸へ"
date: "2026-07-22"
source: "digest/DeepDive/2026-07-22-DeepDive.md"
type: "deepdive-dialogue"
audio_target_minutes: 5
context_sources:
  - date: "2026-06-29"
    title: "Gemini制限、AI計算資源の主戦場"
    relation: "主役共有"
roles:
  senior:
    model_uuid: "47e53151-a378-46f3-abee-ce13aa07feb1"
  junior:
    model_uuid: "59f96896-64d2-4378-830a-4d5feb3d81aa"
---

## 台本

<!-- value:current_signal evidence:source:0 support:source:1 -->

若手: 「Googleは7月21日、Gemini3.6Flash、3.5Flash-Lite、3.5FlashCyberを同時に発表した。」という記述で、以前と違う対象はどこですか。

先輩: Googleは7月21日、Gemini3.6Flash、3.5Flash-Lite、3.5FlashCyberを同時に発表した。 対照になる材料は、高品質な司令塔、高速・低単価の実行役、脆弱性対応の専門役を分けた構成で、企業の選定軸を「最強の1モデル」から「仕事ごとの役割分担」へ移す狙いが見える。 両者の対象と時点の差が、今回更新された認識です。

<!-- value:evidence evidence:source:2 support:source:3 -->

若手: 「GoogleCloudは自社のGeminiを売る一方、AnthropicのClaudeもVertexAIで管理APIとして配布してきた。」を、別の確認済み材料で照合できますか。

先輩: GoogleCloudは自社のGeminiを売る一方、AnthropicのClaudeもVertexAIで管理APIとして配布してきた。 独立して照合する材料は、モデル層では競い、基盤層では顧客の選択肢を増やす関係である。 この二件は主体・数値・日付を分けて記録できます。

<!-- value:causal_chain evidence:source:4 support:source:5 -->

若手: 「企業側にとってはベンダーの勝敗より、同じ統制面で複数モデルを切り替えられるかが重要になる。」の後に観測された結果は何ですか。

先輩: 前提として、企業側にとってはベンダーの勝敗より、同じ統制面で複数モデルを切り替えられるかが重要になる。 観測された結果は、Gemini3.6FlashのGDPval-AAv2は1421で、3.5Flashの1349を上回った。 前者から後者までを記事が示す範囲の因果として扱います。

<!-- value:counterevidence_or_limit evidence:source:6 support:source:7 -->

若手: 「DeepSWEも37%から49%へ伸び、Googleは出力tokenを3.5比で17%削減したと説明する。」を確定事項にしすぎない境界はどこですか。

先輩: 確認済みの範囲は、DeepSWEも37%から49%へ伸び、Googleは出力tokenを3.5比で17%削減したと説明する。 ただし、品質向上だけでなく、同じ業務を少ないtokenと少ない実行ループで終えることが採算の焦点になる。 この二件に書かれていない将来結果は未確定として残します。

<!-- value:change_over_time evidence:source:8 support:source:9 -->

若手: 「Gemini3.5Flash-Liteは350outputtokens/s、入力0.30ドル・出力2.50ドルで、3.6Flashの5分の1、3分の1の単価である。」から、次の段階へ何が移りましたか。

先輩: 当日の基準点は、Gemini3.5Flash-Liteは350outputtokens/s、入力0.30ドル・出力2.50ドルで、3.6Flashの5分の1、3分の1の単価である。 移動先を示す材料は、1Mcontextと64k最大出力は共通だ。 2026-06-29の「Gemini制限、AI計算資源の主戦場」では、前回はOpenAIのCodex利用拡大から、AIエージェントが業務OSへ入る流れを見た。 今回との差分は、前回は「Gemini制限、AI計算資源の主戦場」として同じ主役の論点を扱い、今回は「GeminiFlash、役割分担が選定軸へ」として業務への埋め込み方に焦点が移った。 対象・時点・判断基準の移動として比較します。

<!-- value:decision_implication evidence:source:10 support:source:11 -->

若手: 「司令塔を3.6、抽出・分類・並列サブタスクをLiteへ振る構成が、単一モデル採用より費用対効果を測りやすい。」を顧客判断へ持ち込む際、分けるべき選択条件は何ですか。

先輩: 選択肢を作る根拠は、司令塔を3.6、抽出・分類・並列サブタスクをLiteへ振る構成が、単一モデル採用より費用対効果を測りやすい。 条件を具体化する材料は、VertexAIでGeminiとClaudeが並ぶ関係図の構図は、Google自身が単一モデル固定より選択肢を基盤価値として売ることを示す。 対象、前提、撤回条件を別々に置いて比較します。

<!-- value:next_action evidence:source:12 support:source:13 -->

若手: 「Flash内部の分業は、競合モデルも含むrouter設計へつながる。」を受け、次の会議までに誰が何を確認しますか。

先輩: 確認対象は、Flash内部の分業は、競合モデルも含むrouter設計へつながる。 照合先は、同じ監視面で完了単価・失敗率・待ち時間を比較できるかが、囲い込みと顧客選択の分岐点になる。 両方に担当者と期限を置き、差分が出た時点で判断条件を更新します。
