# CCTranslationTool

Windows 用の常駐翻訳ツールです。テキストを選択した状態で `Ctrl + C` を 2 回押すと、Google 翻訳の Web API を利用して取得した訳文をポップアップ表示します。

## セットアップ

1. Python 3.9 以上をインストールします。
2. このリポジトリをクローンし、必要な依存関係をインストールします。

   ```bash
   pip install -r requirements.txt
   ```

## 使い方

1. アプリを起動します。

   ```bash
   python translator_app.py --dest ja
   ```

   - `--dest` は翻訳先の言語コードです（デフォルトは `ja`）。
   - `--src` を指定すると翻訳元の言語を固定できます。未指定の場合は自動判別されます。

2. 翻訳したいテキストを選択し、`Ctrl + C` を素早く 2 回押します。
3. ポップアップに原文と訳文が表示されます。

> **注意:** `keyboard` ライブラリは管理者権限を要求する場合があります。権限が不足しているとホットキーを正しく検出できないので、その際は管理者として実行してください。

## テスト

Windows 上での動作を確認する際は、アプリ本体を実行する前にユニットテストを実行してください。

```bash
python -m unittest discover
```

テストではホットキー検知や翻訳処理をモック化しており、ダブルコピー判定やエラーハンドリングの動作を検証できます。

## Windows 用実行ファイルの作成

`PyInstaller` を利用して 1 つの実行ファイルにまとめるためのスクリプトを `packaging/build_executable.py` に用意しています。Windows 環境で以下の手順を実行してください。

1. 依存関係に加えて `PyInstaller` をインストールします。

   ```bash
   pip install -r requirements.txt pyinstaller
   ```

2. パッケージングスクリプトを実行します。

   ```bash
   python packaging/build_executable.py
   ```

スクリプトは `package` ディレクトリを作成し、その中に `CCTranslationTool.exe` を出力します。生成される実行ファイルはコンソールウィンドウを表示せずにバックグラウンドで起動します。PyInstaller が生成する一時的な `build/` や `dist/` ディレクトリ、`.spec` ファイルは自動的に削除されます。
