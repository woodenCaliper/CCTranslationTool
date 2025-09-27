# CCTranslationTool

CCTranslationTool は Windows 向けの常駐型翻訳ユーティリティです。選択中のテキストをコピーしたあと素早く `Ctrl + C` を 2 回押すと、Google 翻訳の Web API から取得した訳文をポップアップ表示します。ホットキーの監視から翻訳結果の表示までをワンストップで行えるように設計されています。

## 主な機能

- クリップボードを監視し、短時間に 2 回コピーされたテキストのみを翻訳
- Google 翻訳 (非公式 API) を利用した高速な翻訳
- 自動言語判別 (`--src` 未指定時)
- 翻訳結果を Tk ウィンドウでポップアップ表示 (原文・訳文・検出言語を表示)
- ダブルコピー検出や翻訳処理を個別にテスト可能なユニットテストを同梱
- PyInstaller を利用した Windows 用単一実行ファイルの作成スクリプトを同梱

## 動作要件

- Windows 10 以降を推奨
- Python 3.9 以上
- クリップボード操作およびグローバルホットキーの検出に管理者権限が必要になる場合があります
- Tkinter (標準ライブラリ) が利用できること
- インターネット接続 (Google 翻訳 API へのアクセスに必要)

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

2. 翻訳したいテキストを選択した状態で `Ctrl + C` を素早く 2 回押します。ダブルコピーが認識されると、原文と訳文がポップアップに表示されます。ウィンドウは `Esc` キーまたは Close ボタンで閉じることができます。

> **注意:** `keyboard` ライブラリはグローバルホットキーを取得するため、管理者権限が必要になる場合があります。権限不足の際は管理者として実行してください。

## テスト

ユニットテストでダブルコピー検知や翻訳処理のエラーハンドリングを確認できます。

```bash
python -m unittest discover
```

## Windows 用実行ファイルのビルド

`packaging/build_executable.py` は PyInstaller を利用して単一の `.exe` ファイルを生成するスクリプトです。Windows 環境で以下の手順を実行してください。

1. 依存関係に加えて PyInstaller をインストールします。

   ```bash
   pip install -r requirements.txt pyinstaller
   ```

2. パッケージングスクリプトを実行します。

   ```bash
   python packaging/build_executable.py
   ```

   生成された `CCTranslationTool.exe` は `package/` ディレクトリに配置され、PyInstaller が生成した一時ディレクトリや `.spec` ファイルは自動的に削除されます。

## トラブルシューティング

- **ホットキーが反応しない:** 管理者権限で実行しているか確認してください。それでも解決しない場合は、他のキーボードフック系ソフトウェアと競合していないか確認してください。
- **翻訳に失敗する:** ネットワーク接続を確認してください。Google 翻訳 API からの応答が変化した場合やアクセス制限が掛かった場合は `translation_service.py` の `GoogleTranslateClient` でエラーが発生することがあります。
- **ウィンドウが表示されない:** Tkinter がインストールされているか、または GUI を表示できる環境 (Windows デスクトップ) で実行しているか確認してください。

## ライセンス

本リポジトリのライセンス情報についてはプロジェクトルートにある LICENSE ファイルを参照してください (存在する場合)。
