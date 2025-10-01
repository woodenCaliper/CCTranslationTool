# ハング調査メモ

## 想定症状
- 起動後、翻訳ウィンドウ上の操作が応答しなくなる
- ホットキーのダブルコピーが効かなくなる

## 変更点
- `translator_app.py`: 翻訳ウィンドウの Tk `mainloop` をメインスレッドで実行するように変更 (commit 3fc1f648)

## 主な懸念点
1. `_process_single_request` が `_lock` を保持したままネットワーク越し翻訳 (`translate`) を待機し、UI スレッドのボタンハンドラ `_toggle_language` などが同じロックでブロックして UI が固まる恐れがある。
2. `CCTranslationApp.start` の再起動ループで `self._keyboard.unhook_all()` を呼ぶと、`PyWinhookKeyboardAdapter` のポンプスレッドが停止したまま再利用され、ホットキーが二度と発火せず「ハング」したように見える。
3. `_handle_copy_event` でも `_lock` を保持したまま `pyperclip.paste()` を呼び出しており、クリップボード API がハングすると UI 操作も巻き込まれる可能性がある。

