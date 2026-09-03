<!-- Copyright (c) 2026 4dcitygml -->
<!-- SPDX-License-Identifier: Apache-2.0 (this README only; vendored schemas keep their own licenses) -->

# schemas/ — オフライン XSD 検証用スキーマ（ベンダリング）

`scripts/validate_citygml.py` が **ネット非依存**で CityGML/PLATEAU を検証するための、
第三者 XSD スキーマのローカルミラーです。

## 構成

- `schemas.opengis.net/` — **CityGML 2.0** 各モジュール ＋ **GML 3.1.1**（OGC 配布）
- `www.w3.org/` — xlink / SMIL 2.0（W3C）
- `docs.oasis-open.org/` — xAL 2.0（OASIS）
- `master.xsd` — 上記全名前空間 ＋ 同梱 i-UR（2.0/3.0/3.1/3.2）を import する検証用ルート。
  スキーマ内の `http(s)://` 参照は `validate_citygml.py` の lxml Resolver がこのミラーへ解決する。
- i-UR（`uro/2.0〜3.2` `urf/2.0〜3.2`）: 3.0〜3.2 は PLATEAU 公式配布 zip 同梱の `schemas/iur/` から取得。
  2.0（2020〜2021 年度データが使う版。例: 東京都23区 2020 v4）は公式スキーマ置き場
  https://www.geospatial.jp/iur/schemas/ から取得（2026-09-02）。1.4/1.5 は非公開のため同梱しない
  （現行の v4 パッケージを再取得する）。4.0 は CityGML 3.0 向けで本検証器の対象外。

実データ（PLATEAU 配布 GML・数十 MB / 千棟級）が `valid=True`（約1.3秒）になることを
確認済み。ネット遮断下でもコンパイル・検証できる。

## 取得元・更新

ファイルは各配布元から `xsd:import`/`include` を再帰的に辿って取得したもの（閉包 51 ファイル）。
実 gml の `xsi:schemaLocation` と i-UR の import が指す URL に一致する。更新時は同じ手順で再取得する。

## ライセンス（重要）

**このディレクトリ内の各 .xsd は第三者の著作物**で、それぞれの配布元ライセンスに従います
（本リポジトリの Apache-2.0 は適用されません）:

- OGC schemas（CityGML / GML）: OGC のスキーマ利用条件（再配布可）
- W3C（xlink / SMIL）: W3C ソフトウェア/ドキュメントライセンス
- OASIS（xAL）: OASIS のスキーマ利用条件

検証の利便のために同梱（ベンダリング）しているものであり、原本の権利は各団体に帰属します。
