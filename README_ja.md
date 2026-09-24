# crawl4tools

[English README is here](README.md)

## crawl4tools とは

crawl4tools は [crawl4ai](https://github.com/unclecode/crawl4ai) ライブラリを基盤にしたWebクローラーです。次の3つの用途で使えるように設計されています。

1. **Open WebUI の external Web loader** として直接使えるHTTPサーバー。Open WebUI の Web検索で取得したURLの本文をMarkdownで返します。Open WebUI 側は `WEB_LOADER_ENGINE=external` と `EXTERNAL_WEB_LOADER_URL` を設定するだけで利用できます。
2. **MCPサーバー**。Claude Code などのAIエージェントから、要約ではなく**全文**を取得できるWeb fetchツールとして機能します。
3. **ローカルCLI**。URLを指定してMarkdown等の形式でダウンロードします。複数URLの一括ダウンロードにも対応する予定です。

## 現状

**Alpha。** このリリース(0.1.0)ではローカルCLI `crawl4cli` が使えます。Open WebUI 用ローダーと MCP サーバーはまだ実装されていません。

## 機能

利用できるもの(CLI):

- 1つまたは複数のURLを Markdown、HTML、PDF、スクリーンショット(PNG)、MHTML、元データのままの形式でダウンロード
- PDF は Markdown に文字起こし。画像などHTML以外のファイルはそのまま保存
- HTTPエラー、ホスト名の解決失敗、接続拒否、タイムアウト、ブラウザ未導入を区別した分かりやすいエラーメッセージ
- HTTP/HTTPS/SOCKS5 プロキシ経由のダウンロード。プロキシ自体の障害時は直接通信で1回だけ再試行

予定:

- Open WebUI external web loader と MCP サーバー、Docker Compose による運用
- プロキシのSSL bumpで結果が壊れる場合の直接通信へのフォールバック

## インストール

CLI には Python 3.11 以上と [uv](https://docs.astral.sh/uv/) が必要です。

```sh
uv tool install --with-executables-from playwright git+https://github.com/xhighhongo41/crawl4tools
playwright install chromium   # ヘッドレスブラウザをダウンロード(初回のみ)
```

ブラウザは Playwright のキャッシュディレクトリ(macOS なら `~/Library/Caches/ms-playwright` など)に保存されます。また crawl4ai が自身のデータ用に `~/.crawl4ai` ディレクトリを作成します。

サーバー(Docker Compose)はまだ利用できません。

## 使い方

```sh
crawl4cli https://example.com/                    # Markdown を標準出力へ
crawl4cli -o page.md https://example.com/         # ファイルに保存
crawl4cli -d out/ URL1 URL2 URL3                  # 複数URLをディレクトリへ
crawl4cli -f screenshot https://example.com/      # example.com.png を保存
crawl4cli --proxy http://proxy.local:8080 URL     # プロキシ経由
```

| オプション | 意味 |
|---|---|
| `-f, --format` | `markdown`(既定)、`html`、`pdf`、`screenshot`、`mhtml`、`raw` |
| `-o, --output FILE` | 単一URLを標準出力ではなく FILE に保存 |
| `-d, --output-dir DIR` | 複数URLとバイナリ形式の保存先(既定: カレントディレクトリ) |
| `--proxy URL` | `http://`、`https://`、`socks5://` のプロキシ。認証情報は `user:pass@host:port` |
| `--no-fallback` | プロキシ障害時に直接通信で再試行しない |
| `-j, --concurrency N` | 同時に取得するURL数(既定: 3) |
| `--timeout SECONDS` | URLごとのページ読み込みタイムアウト(既定: 60) |
| `--fit` | 本文だけを残す(メニューやフッターなどを除く)。何も残らなかった場合はページ全体を出力 |
| `--citations` | リンクを番号付き参照にし、末尾に一覧を付ける |
| `--no-links`, `--no-images` | Markdown からリンクや画像参照を除く |
| `-q, --quiet` / `-v, --verbose` | 標準エラーへの出力を減らす/増やす |

標準出力に出るのは本文だけで、通知・エラー・要約は標準エラーに出ます。ファイル名はURLから作られます(`https://example.com/a/b` → `example.com_a_b.md`)。終了コードは、全URL成功で 0、1件でも失敗で 1、引数の誤りで 2 です。すべてのオプションは `CRAWL4CLI_<OPTION>` という名前の環境変数でも指定できます(例: `CRAWL4CLI_PROXY`)。標準の `HTTP_PROXY`/`HTTPS_PROXY` 環境変数は使いません。

## 謝辞

This product includes software developed by UncleCode (https://x.com/unclecode) as part of the Crawl4AI project (https://github.com/unclecode/crawl4ai). Crawl4AI は Apache License 2.0 のもとで公開されています。

## ライセンス

本プロジェクトは Apache License 2.0 のもとで公開されています。詳細は [LICENSE](LICENSE) ファイルを参照してください。
