# 状態更新→再描画→副作用ループのリスク分析

## ループ疑い箇所

1. **言語切替→再翻訳→UI更新ループ**  
   `_toggle_language` や `_set_dest_language` は言語状態を更新すると同時に `_enqueue_retranslation` を呼び出し、直前の原文を再キューイングします。【F:translator_app.py†L1033-L1067】【F:translator_app.py†L1161-L1168】  
   React 側で `display_callback` から渡ってくる訳文・言語を `useEffect` 経由でアプリ本体へ反映する実装の場合、依存配列に `destLanguage` を含めたまま `setDestLanguage` を常に呼ぶと再翻訳が無限連鎖します。

2. **翻訳結果のステート反映→再レンダ→副作用再実行**  
   `_render_translation` は毎回 `display_callback` を呼び出すため、React 側で `setState` を無条件に実行すると親コンポーネントまで再レンダが伝播し、副作用内で `requestTranslation` のような I/O を叩く実装だと再度 `_enqueue_retranslation` が起動する可能性があります。【F:translator_app.py†L1134-L1168】

3. **ソース言語選択ダイアログ→外部ステート同期→再表示**  
   `_set_source_language` は UI からの選択に応じて再翻訳をキューに積み直します。【F:translator_app.py†L1068-L1076】【F:translator_app.py†L1161-L1168】  
   React 側で "検出言語" を props から state にコピーする際、前回と同一かを比較しないと `useEffect` が再び `setSourceLanguage` を呼び、再レンダ→副作用→再翻訳のループを誘発します。

## 修正方針

### useEffect 依存配列の見直し
- 翻訳結果を受け取るコンポーネントでは `useEffect(() => { ... }, [translatedText, detectedSource])` のように必要最小限の依存に絞り、`destLanguage` や UI フラグをむやみに含めない。
- 言語変更をアプリ本体へ伝播する副作用では `if (nextDest !== prevDestRef.current) { sendDest(nextDest); prevDestRef.current = nextDest; }` のようにガードを入れてから副作用を実行する。

### メモ化と状態同一性比較
- `display_callback` を受け取る React 側の `useCallback` / `useMemo` を活用し、子コンポーネントへの props が安定するようにする。これにより子の `useEffect` が不要な再実行を避けられる。
- 翻訳結果を保持する state は `useState` に加え `useRef` を併用し、同一文字列であれば `setState` をスキップする。同一性比較には `Object.is` かハッシュ化済み文字列を使い、巨大テキストの再レンダを防ぐ。

### I/O やコールバック呼び出しの抑制
- `requestTranslation` のような副作用は `useEffect` 内でリトライ制御し、"実行中" フラグが変わらない限り再発火しないよう `useRef` で実行中状態を保持する。
- 言語切替後に UI 側で `setState` する場合も、変更が確定したタイミング（例: メニューを閉じた後）だけ送出するようにし、`onChange` での逐次送信を避ける。

## 10 秒間の再レンダ計測インストゥルメント
- 環境変数 `CCTRANSLATION_RENDER_DIAGNOSTICS` を有効化すると、`_render_counter` が 10 秒ごとに `_render_translation` 呼び出し回数を集計し `[RENDER]` ログを出力します。【F:translator_app.py†L63-L76】【F:translator_app.py†L1134-L1168】
- 終了時には `atexit` フックで直近のカウントをフラッシュするため、短時間の再現テストでも集計が失われません。【F:translator_app.py†L168-L209】

