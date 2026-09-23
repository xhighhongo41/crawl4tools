# crawl4tools

[English README is here](README.md)

## crawl4tools とは

crawl4tools は [crawl4ai](https://github.com/unclecode/crawl4ai) ライブラリを基盤にしたWebクローラーです。次の3つの用途で使えるように設計されています。

1. **Open WebUI の external Web loader** として直接使えるHTTPサーバー。Open WebUI の Web検索で取得したURLの本文をMarkdownで返します。Open WebUI 側は `WEB_LOADER_ENGINE=external` と `EXTERNAL_WEB_LOADER_URL` を設定するだけで利用できます。
2. **MCPサーバー**。Claude Code などのAIエージェントから、要約ではなく**全文**を取得できるWeb fetchツールとして機能します。
3. **ローカルCLI**。URLを指定してMarkdown等の形式でダウンロードします。複数URLの一括ダウンロードにも対応する予定です。

## 現状

**Pre-alpha。まだ動くものは何もありません。** このリリース(0.0.1)はライセンス・文書・プロジェクト設定のみを含み、コードの実装はまだ行われていません。

## 予定している機能

- Open WebUI external web loader / MCPサーバー / ローカルCLI の3形態での利用
- crawl4aiが提供する出力形式(Markdown, raw HTML, PDF, screenshot, MHTML)の選択
- HTTP/HTTPSプロキシ経由のダウンロード、およびプロキシ障害やSSL bumpで結果が壊れるサイトでの直接通信への自動フォールバック
- Docker Composeによるサーバーの運用
- `uv tool install` 等によるCLIの一発インストール

## インストール

まだ利用できません。リリース後は、サーバーはDocker Compose、CLIは `uv tool install` 等での一発インストールに対応する予定です。

## 使い方

まだ利用できません。サーバー、MCPサーバー、CLIの実装完了後に使用方法を追記します。

## 謝辞

This product includes software developed by UncleCode (https://x.com/unclecode) as part of the Crawl4AI project (https://github.com/unclecode/crawl4ai). Crawl4AI は Apache License 2.0 のもとで公開されています。

## ライセンス

本プロジェクトは Apache License 2.0 のもとで公開されています。詳細は [LICENSE](LICENSE) ファイルを参照してください。
