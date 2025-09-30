# CCTranslationTool

CCTranslationTool は Windows 向けの常駐型翻訳ユーティリティです。選択中のテキストをコピーしたあと素早く `Ctrl + C` を 2 回押すと、Google 翻訳の Web API から取得した訳文をポップアップ表示します。ホットキーの監視から翻訳結果の表示までをワンストップで行えるように設計されています。Windows 11 では Win32 API の RegisterHotKey を使用してグローバルホットキーを安定的に取得します。

## 主な機能

- クリップボードを監視し、短時間に 2 回コピーされたテキストのみを翻訳 (既定設定、変更可能)
- Google 翻訳 (非公式 API) を利用した高速な翻訳
- 自動言語判別 (`--src` 未指定時)
- 翻訳結果を Tk ウィンドウでポップアップ表示 (原文・訳文・検出言語を表示)
- ダブルコピー検出や翻訳処理を個別にテスト可能なユニットテストを同梱
- PyInstaller を利用した Windows 用単一実行ファイルの作成スクリプトを同梱
- RegisterHotKey ベースのホットキー監視により、IME 切替・スリープ復帰・ユーザー切替後も自動で再登録
- 状態ダンプホットキー (既定: `F8`) で登録状況とイベントをログ出力

## 動作要件

- Windows 10 以降を推奨
- Python 3.9 以上
- クリップボード操作およびグローバルホットキーの検出は標準権限で動作する想定です (一部の管理者アプリが前面にある場合は動作しない場合があります)
- Tkinter (標準ライブラリ) が利用できること
- インターネット接続 (Google 翻訳 API へのアクセスに必要)
- pywin32 (Win32 API 呼び出し)

## セットアップ

1. Python 3.9 以上をインストールします。
2. 任意 (推奨): 仮想環境を作成して有効化します。

   ```bash
   python -m venv .venv
   .venv\\Scripts\\activate        # PowerShell の場合
   # または
   .venv\\Scripts\\activate.bat    # コマンドプロンプトの場合
   ```

3. 依存関係をインストールします。

   ```bash
   pip install -r requirements.txt
   ```

## 使い方

1. アプリケーションを起動します。

   ```bash
   python translator_app.py --dest ja
   ```

   - `--dest`: 翻訳先の言語コード (Google 翻訳の言語コードを使用)。デフォルトは `ja`。
   - `--src`: 翻訳元の言語コード。未指定時は自動判別します。

2. 翻訳したいテキストを選択した状態で `Ctrl + C` を素早く 2 回押します (既定設定)。ダブルコピーが認識されると、原文と訳文がポップアップに表示されます。ウィンドウは `Esc` キーまたは Close ボタンで閉じることができます。
3. `F8` (既定) を押すと、現在のホットキー登録状況をログファイルに出力できます。

> **ヒント:** ホットキーは設定ファイルで変更できます。IME の影響を避けたい場合は `Ctrl+Alt+T` などの組み合わせを指定してください。

## ホットキー設定とログ

- 設定ファイル: `%USERPROFILE%\\.cctranslationtool_preferences.json`
- 例:

  ```json
  {
    "dest_language": "ja",
    "hotkeys": {
      "copy": {
        "combo": "Ctrl+C",
        "press_count": 2
      },
      "state_dump": {
        "combo": "F8",
        "press_count": 1
      },
      "double_press_interval": 0.25,
      "min_trigger_interval": 0.15
    }
  }
  ```

- `combo` は `Ctrl+Alt+T` や `Shift+F2` などの表記を使用します。`press_count` を 1 にすると単押しでトリガーできます。
- `double_press_interval` はダブル押下を判定する時間 (秒)、`min_trigger_interval` は連続実行を抑制する最小間隔 (秒) です。
- ログファイル: `%USERPROFILE%\\cctranslationtool_hotkeys.log`
  - RegisterHotKey の登録 / 再登録、IME 切替、スリープ復帰、例外などを循環ログ (約 2 MB × 3 世代) で記録します。
  - 状態ダンプ (`F8`) を押すと、現在の登録内容とキュー状態を記録します。

> 設定を変更した場合はアプリを再起動してください。設定値に誤りがある場合はログに記録され、既定値で起動します。

## テスト

ユニットテストでダブルコピー検知や翻訳処理のエラーハンドリングを確認できます。

```bash
python -m unittest discover
```

## Windows 用実行ファイルのビルド

`packaging/build_executable.py` は PyInstaller を利用して Windows 向け実行ファイルを生成するスクリプトです。デフォルトでは PyInstaller の `onedir` モードを採用し、生成されたフォルダーをそのまま配布することで Windows Defender が `Program:Script/Wacapew.A!ml` として誤検知する問題 (PyInstaller の単一ファイルブートストラップに対する既知のヒューリスティック) を回避しています。Windows 環境で以下の手順を実行してください。

1. 依存関係に加えて PyInstaller をインストールします。

   ```bash
   pip install -r requirements.txt pyinstaller
   ```

2. パッケージングスクリプトを実行します。

   ```bash
   python packaging/build_executable.py
   ```

   `package/CCTranslationTool/` フォルダーに必要な DLL を含む実行ファイル一式が配置されます。`CCTranslationTool.exe` を実行する際はこのフォルダー内から起動してください。PyInstaller が生成した一時ディレクトリや `.spec` ファイルは自動的に削除されます。

   既存のワークフローで単一の `.exe` ファイルが必要な場合は、以下のように `--mode onefile` を指定してください。ただし、このモードは前述のヒューリスティック検出を受けやすい点に留意してください。

   ```bash
   python packaging/build_executable.py --mode onefile
   ```

## トラブルシューティング

- **ホットキーが反応しない:** 設定ファイルの `combo` が正しいか、他の常駐ソフトが同じショートカットを占有していないかを確認してください。必要に応じて `Ctrl+Alt+T` などの別の組み合わせに変更してください。
- **IME 切替やスリープ復帰後に動作しない:** `%USERPROFILE%\\cctranslationtool_hotkeys.log` を確認し、再登録の失敗や例外が記録されていないか調べてください。管理者権限アプリの前面では標準権限アプリからの RegisterHotKey がブロックされる場合があります。
- **翻訳に失敗する:** ネットワーク接続を確認してください。Google 翻訳 API からの応答が変化した場合やアクセス制限が掛かった場合は `translation_service.py` の `GoogleTranslateClient` でエラーが発生することがあります。
- **ウィンドウが表示されない:** Tkinter がインストールされているか、または GUI を表示できる環境 (Windows デスクトップ) で実行しているか確認してください。

## 動作確認のポイント

1. Windows 11 + 日本語 IME 環境でダブルコピーが動作すること。
2. 半角/全角キーで IME を切り替えた直後でもホットキーが動作し続けること。
3. スリープ復帰・画面ロック解除・ユーザー切替後にログへ再登録が記録され、ホットキーが復活すること。
4. 高負荷時にホットキー押下が 100ms 程度で翻訳キューに積まれること (ログで確認できます)。
5. `F8` で状態ダンプを出力すると、登録済みホットキーやキュー長がログに記録されること。

## ライセンス

本リポジトリのライセンス情報についてはプロジェクトルートにある LICENSE ファイルを参照してください (存在する場合)。

## 免責事項

本ソフトウェアおよび関連ドキュメントは現状有姿のまま提供されており、明示または黙示を問わず、商品性、特定目的への適合性、および権利非侵害に関する保証を含むいかなる保証も行いません。本ソフトウェアの使用または使用不能により生じたあらゆる損害・損失について、作成者は一切の責任を負いません。
