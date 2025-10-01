# CCTranslationTool 要求仕様

## 概要
- Windows 常駐型の翻訳ユーティリティであり、ユーザーが `Ctrl + C` を短時間で 2 回押すとクリップボードのテキストを翻訳しポップアップ表示する。【F:README.md†L1-L20】【F:translator_app.py†L241-L319】
- Google 翻訳の Web API を利用し、翻訳元言語の自動判別および任意の翻訳先言語指定をサポートする。【F:README.md†L10-L20】【F:translator_app.py†L70-L118】【F:translation_service.py†L1-L120】
- ユニットテストおよび PyInstaller を利用した Windows 用実行ファイルの生成スクリプトを提供する。【F:README.md†L21-L92】

## 対象プラットフォームと依存関係
- 推奨 OS: Windows 10 以降。Python 3.9 以上が必要。【F:README.md†L22-L35】
- クリップボード操作 (pyperclip) とグローバルホットキー取得 (pyWinhook) を使用。Tkinter による GUI 表示に対応している環境が前提。【F:translator_app.py†L8-L117】【F:README.md†L22-L49】
- グローバルキーボードフックは pyWinhook で実装し、Windows API レベルで `Ctrl + C` 押下イベントを取得できることを必須要件とする。【F:keyboard_adapter.py†L1-L160】【F:translator_app.py†L108-L209】
- 任意機能として pystray と Pillow によるシステムトレイアイコン表示に対応。依存関係が不足する場合は自動的に機能を無効化する。【F:translator_app.py†L30-L209】【F:translator_app.py†L459-L542】

## 機能要件
### ホットキー監視とコピー検出
1. `Ctrl + C` が短時間 (既定 0.5 秒以内) に 2 回押されたときのみ翻訳要求を生成する。間隔は設定可能とし、ダブルコピー検出ユーティリティで管理する。【F:translator_app.py†L54-L166】【F:translator_app.py†L543-L655】
2. グローバルホットキーの検知は pyWinhook を介して行い、Windows のメッセージループを用いて安定的にキーダウン・キーアップイベントを監視する。【F:keyboard_adapter.py†L1-L160】【F:translator_app.py†L108-L209】
3. クリップボードから取得したテキストが空の場合や前回と同一の場合は翻訳をスキップする。【F:translator_app.py†L656-L792】

### 翻訳処理
1. Google 翻訳クライアントを用いてテキストを翻訳し、翻訳元言語を自動検出する。`--src` が指定された場合はその言語を優先する。【F:translation_service.py†L1-L154】【F:translator_app.py†L70-L166】
2. 翻訳結果はキューを介して UI スレッドに渡され、原文・訳文・検出言語を表示する。【F:translator_app.py†L210-L451】【F:translator_app.py†L656-L792】
3. 翻訳先言語はユーザー設定を保存し、再起動時に復元する。【F:translator_app.py†L38-L68】【F:translator_app.py†L543-L655】

### ユーザーインターフェース
1. Tkinter 製のポップアップウィンドウは初期サイズ 500×400 で生成され、コントロールエリアには検出言語・トグル・翻訳先の 3 つのボタンを横並びで配置し、フォントや配色を揃えた一貫した外観を採用する。【F:translator_app.py†L288-L357】
2. 原文と訳文は上下 2 分割されたスクロール可能テキスト領域に読み取り専用で表示し、それぞれにラベルを付与して内容を識別しやすくする。【F:translator_app.py†L361-L382】
3. ダブルコピー検出時にウィンドウが表示され、`Esc` キーまたはクローズボタンで非表示に戻る。表示時は最新の翻訳内容でテキスト領域を更新する。【F:translator_app.py†L383-L397】【F:translator_app.py†L647-L665】
4. 言語トグルボタンで翻訳方向を入れ替え、プルダウンメニューから `auto`/`ja`/`en` を選択して検出言語と翻訳先言語を明示的に設定できる。【F:translator_app.py†L431-L460】
5. ポインタ付近への再配置や最前面化、フォーカス獲得を自動で行い、必要に応じて Windows 上で強制的に前面表示することで翻訳結果を即座に確認できる。【F:translator_app.py†L553-L645】
6. システムトレイアイコンが利用可能な場合は再起動と終了コマンドを提供し、常駐動作時の操作性を補完する。【F:translator_app.py†L690-L720】
7. 既定の言語シーケンスは日本語と英語であり、ユーザー操作で順次切り替えられる。【F:translator_app.py†L70-L118】【F:translator_app.py†L656-L792】
8. 翻訳結果を表示する Tk ウィンドウはウィンドウマネージャによって単一インスタンスとして生成・再利用され、イベントループも多重起動しないよう保護されている。複数回の `Ctrl + C` トリガーが短時間に発生してもキュー処理で順次更新し、既存ウィンドウを再描画するだけで重複生成や表示の破綻が起こらない。【F:translator_app.py†L203-L287】【F:translator_app.py†L243-L280】【F:translator_app.py†L647-L675】

### コマンドラインインターフェース
1. `translator_app.py` は `--dest` (翻訳先)、`--src` (翻訳元)、`--double-copy-interval` などのオプションを受け付ける。【F:translator_app.py†L656-L792】
2. `--once` オプションで単発翻訳を実行し、標準出力に結果を表示するモードを提供する。【F:translator_app.py†L656-L792】
3. CLI は単一インスタンスガードを備え、既に起動中のインスタンスがある場合には起動を拒否してユーザーに通知することで、アプリケーション自体の多重起動を防ぐ。【F:translator_app.py†L142-L200】【F:translator_app.py†L1007-L1019】

### エラーハンドリングと通知
1. 翻訳失敗時はユーザーにエラーダイアログを表示し、再試行が可能である。【F:translator_app.py†L656-L792】
2. 必須モジュールが欠落している場合は起動時に明示的なエラーメッセージを出して終了する。【F:translator_app.py†L70-L166】
3. 翻訳サービスからのエラーは例外として扱い、ユーザーへの通知とログ出力 (標準出力/標準エラー) を行う。【F:translator_app.py†L656-L792】

## 非機能要件
- 翻訳処理はバックグラウンドスレッドで実行し、UI スレッドをブロックしない。【F:translator_app.py†L543-L655】【F:translator_app.py†L656-L792】
- クリップボード監視・翻訳処理の各コンポーネントをユニットテスト可能とする構成を提供する。【F:README.md†L13-L20】【F:tests/test_translator_app.py†L1-L200】
- PyInstaller を用いた配布時には単一インスタンス制御やリソースパス解決が正常に機能するよう、`_resource_path` で `_MEIPASS` を考慮する。【F:translator_app.py†L49-L117】【F:README.md†L64-L92】

## 運用・保守要件
- 実行にはインターネット接続が必要であり、API 変更やアクセス制限への対処として `translation_service.py` の調整が必要な場合がある。【F:README.md†L36-L63】【F:translation_service.py†L1-L154】
- トラブルシューティングとして権限不足やネットワーク障害への対処手順を提供する。【F:README.md†L93-L117】
- ライセンス情報および免責事項は README に従う。【F:README.md†L118-L136】
