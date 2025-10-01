# CCTranslationTool 現行実装概要

本ドキュメントは、CCTranslationTool の主要なモジュール構成とフローを把握し、フリーズ調査の足掛かりとするために作成した実装サマリです。

## 全体構成

- `translator_app.py`
  - GUI、キーボードフック、翻訳リクエスト処理を統括するエントリーポイント。
  - Tkinter を用いた翻訳結果ウィンドウと、Windows 向けのシステムトレイ制御を含む。
- `translation_service.py`
  - Google Translate の非公式 Web API を叩く最小限の HTTP クライアントと結果モデルを提供。
- `keyboard_adapter.py`
  - Windows 専用の pyWinhook ベースのキーボードフックをラップし、`ctrl+c` の連続検出に利用。

## 起動・終了フロー

1. `main()` で `SingleInstanceGuard` を用いて多重起動を防止し、引数解析後に `CCTranslationApp` を生成します。【F:translator_app.py†L935-L979】
2. `CCTranslationApp.start()` がホットキー登録、キュー処理スレッド起動、Tk メインループ（もしくは外部表示コールバック）を制御します。【F:translator_app.py†L781-L856】
3. 終了時はホットキーを解除し、システムトレイを停止してループを抜けます。【F:translator_app.py†L821-L853】

## 入力監視とリクエスト生成

- `DoubleCopyDetector` がコピー操作の時間間隔を監視し、所定のインターバル内に 2 回 `ctrl+c` が発生したら翻訳要求を発火します。【F:translator_app.py†L84-L140】【F:translator_app.py†L877-L904】
- クリップボード (`pyperclip`) から取得したテキストを `TranslationRequest` としてキューに積み、空文字や例外時は検出器をリセットします。【F:translator_app.py†L870-L904】

## 翻訳処理パイプライン

- バックグラウンドスレッド `_process_requests()` がキューを消費し、`_process_single_request()` で翻訳を実行します。【F:translator_app.py†L906-L924】
- `translator` プロパティで遅延初期化した `GoogleTranslateClient` を共有し、エラーは `TranslationError` として UI に表示されます。【F:translator_app.py†L804-L835】【F:translator_app.py†L915-L933】
- 最後に表示した原文を保持し、言語設定変更時に `_enqueue_retranslation()` で再翻訳を自動実行します。【F:translator_app.py†L854-L868】【F:translator_app.py†L954-L961】

## Tk ウィンドウ管理

- `TranslationWindowManager` が単一の Tk ウィンドウを生成し、キューで受け取った翻訳結果を UI スレッド上で描画します。【F:translator_app.py†L198-L691】
- ダブルコピー検出時にウィンドウをカーソル付近へ移動し、フォーカスを奪う処理を行います（Windows 専用の強制前面化含む）。【F:translator_app.py†L566-L639】
- 言語ボタン・トグルの更新や Escape キーでの非表示など、UI の状態管理を内部で完結させています。【F:translator_app.py†L344-L465】【F:translator_app.py†L501-L563】

## システムトレイ制御

- `SystemTrayController` は Windows かつ依存ライブラリが揃っている場合のみ有効化され、再起動・終了メニューを提供します。【F:translator_app.py†L644-L748】
- アイコン画像は同梱リソースまたは描画で生成され、ユーザー操作に応じて `CCTranslationApp` の `stop()` / `reboot()` を呼び出します。【F:translator_app.py†L700-L741】

## 言語設定と永続化

- デフォルトの言語シーケンス（日本語・英語）と、検出言語/翻訳先の表示名を保持します。【F:translator_app.py†L47-L70】
- 翻訳先設定はホームディレクトリ直下の JSON ファイルに保存し、起動時に読み込まれます。【F:translator_app.py†L33-L45】【F:translator_app.py†L71-L83】
- トグルやメニュー操作で言語が変化した場合は、最新の原文に対して再翻訳をキュー投入します。【F:translator_app.py†L835-L868】

## キーボードフック実装

- `create_keyboard_listener()` は Windows で pyWinhook / pythoncom が利用可能なときに `PyWinhookKeyboardAdapter` を返します。【F:keyboard_adapter.py†L21-L44】
- アダプタは専用スレッド上で Windows メッセージループを回し、押下状態を管理しながらホットキーコールバックを実行します。【F:keyboard_adapter.py†L46-L143】

## 翻訳サービス

- `GoogleTranslateClient.translate()` は HTTP GET を構築し、JSON レスポンスを解析して翻訳結果と検出言語を返します。【F:translation_service.py†L21-L70】
- タイムアウト・ネットワークエラー・レスポンス異常は `TranslationError` にラップされ、上位で UI 表示されます。【F:translation_service.py†L34-L69】【F:translator_app.py†L915-L933】

## 単一インスタンス制御

- `SingleInstanceGuard` は一時ディレクトリにロックファイルを作成し、プラットフォームごとのファイルロック API で多重起動を阻止します。【F:translator_app.py†L142-L197】
- 既に起動している場合は Tk のメッセージボックスでユーザーへ通知します。【F:translator_app.py†L967-L979】

## スレッドと同期

- 主スレッド: Tk メインループ（または待機）を実行し、ホットキー登録とシステムトレイ制御を担当。
- バックグラウンドスレッド: 翻訳キュー処理と pyWinhook メッセージループ（Windows）を個別に実行。
- 共有状態（言語設定、最後の原文、翻訳クライアントなど）は `threading.Lock` を介して保護されています。【F:translator_app.py†L794-L868】【F:keyboard_adapter.py†L58-L122】

