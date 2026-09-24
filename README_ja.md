# crawl4tools

[English README is here](README.md)

## crawl4tools とは

crawl4tools は [crawl4ai](https://github.com/unclecode/crawl4ai) ライブラリを基盤にしたWebクローラーです。次の3つの用途で使えるように設計されています。

1. **Open WebUI の external Web loader** として直接使えるHTTPサーバー。Open WebUI の Web検索で取得したURLの本文をMarkdownで返します。Open WebUI 側は `WEB_LOADER_ENGINE=external` と `EXTERNAL_WEB_LOADER_URL` を設定するだけで利用できます。
2. **MCPサーバー**。Claude Code などのAIエージェントから、要約ではなく**全文**を取得できるWeb fetchツールとして機能します。
3. **ローカルCLI**。URLを指定してMarkdown等の形式でダウンロードします。複数URLの一括ダウンロードにも対応する予定です。

## 現状

**Alpha。** このリリース(0.2.0)ではローカルCLI `crawl4cli` と MCPサーバー `crawl4mcp` が使えます。Open WebUI 用ローダーはまだ実装されていません。

## 機能

利用できるもの(CLI):

- 1つまたは複数のURLを Markdown、HTML、PDF、スクリーンショット(PNG)、MHTML、元データのままの形式でダウンロード
- PDF は Markdown に文字起こし。画像などHTML以外のファイルはそのまま保存
- HTTPエラー、ホスト名の解決失敗、接続拒否、タイムアウト、ブラウザ未導入を区別した分かりやすいエラーメッセージ
- HTTP/HTTPS/SOCKS5 プロキシ経由のダウンロード。プロキシ自体の障害時は直接通信で1回だけ再試行

利用できるもの(MCPサーバー):

- 2つのツール: `fetch` はページを Markdown(既定)、HTML、PNGスクリーンショットとしてそのままクライアントに返す(画像は画像として、PDFは Markdown に文字起こしして返る)。`download` は Markdown、HTML、PDF、スクリーンショット、MHTML、元データのいずれかの形式でサーバー上のディレクトリに保存し、保存先パスを返す
- 1回の呼び出しで複数URL(既定の上限20件)を指定でき、サーバー全体の同時実行数の上限(既定3)のもとで並行して取得。ヘッドレスブラウザは1つを共有
- stdio(既定)と Streamable HTTP の両トランスポートに対応
- サーバー側で設定するプロキシと直接通信へのフォールバック
- 設定はコマンドラインオプション、`CRAWL4MCP_*` 環境変数、YAML/JSON設定ファイルのいずれでも可能

予定:

- Open WebUI external web loader
- Docker Compose による運用
- プロキシのSSL bumpで結果が壊れる場合の直接通信へのフォールバック

## インストール

CLI と MCPサーバーには Python 3.11 以上と [uv](https://docs.astral.sh/uv/) が必要です。

```sh
uv tool install --with-executables-from playwright git+https://github.com/xhighhongo41/crawl4tools
playwright install chromium   # ヘッドレスブラウザをダウンロード(初回のみ)
```

これで `crawl4cli` と `crawl4mcp` の両方がインストールされます。

ブラウザは Playwright のキャッシュディレクトリ(macOS なら `~/Library/Caches/ms-playwright` など)に保存されます。また crawl4ai が自身のデータ用に `~/.crawl4ai` ディレクトリを作成します。

Docker Compose による運用は予定していますが、まだ利用できません。

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

## MCPサーバー

`crawl4mcp` は同じ取得エンジンを [MCP](https://modelcontextprotocol.io/) サーバーとして公開し、`fetch`(内容を直接返す)と `download`(ファイルに保存する)の2つのツールを提供します。

### クライアントの接続

[Claude Code](https://docs.claude.com/claude-code) の場合、stdio(既定のトランスポート)では次のようにします。

```sh
claude mcp add --transport stdio crawl4tools -- crawl4mcp
```

Streamable HTTP を使う場合は、先にサーバーを起動してから Claude Code に接続先を指定します。

```sh
crawl4mcp --transport http
claude mcp add --transport http crawl4tools http://127.0.0.1:8765/mcp
```

Claude Desktop の場合は `claude_desktop_config.json` に次のように追記します。

```json
{
  "mcpServers": {
    "crawl4tools": {
      "command": "crawl4mcp",
      "args": ["--download-dir", "/path/to/downloads"]
    }
  }
}
```

Claude Desktop が `crawl4mcp` を見つけられない場合(シェルの PATH が反映されないことがあります)、`"command": "crawl4mcp"` の部分を `which crawl4mcp` で表示されるフルパスに置き換えてください。

Streamable HTTP に対応する他のクライアント(例: Open WebUI の MCP対応)も、`--transport http` でサーバーを起動した上で `http://<host>:<port>/mcp` に接続できます。

### ツール

| ツール | パラメータ |
|---|---|
| `fetch` | `urls`(必須)、`format`(`markdown` 既定、`html`、`screenshot`)、`fit`、`citations`、`ignore_links`、`ignore_images`、`timeout_s` |
| `download` | `urls`(必須)、`format`(`markdown` 既定、`html`、`pdf`、`screenshot`、`mhtml`、`raw`)、`directory`、`fit`、`citations`、`ignore_links`、`ignore_images`、`timeout_s` |

- `urls`: 1つ以上の `http`/`https` のURL。サーバーの1回あたりの上限(`--max-urls`、既定20)まで指定可能。重複するURLは1回だけ取得
- `format`: `fetch` では取得したページをそのまま `markdown`(既定)、`html`、`screenshot`(PNG)のいずれかで返す。`fetch` で取得した画像は画像として、PDFは Markdown に文字起こしして返す。`download` では `markdown`(既定)、`html`、`pdf`、`screenshot`、`mhtml`、`raw` のいずれかの形式でファイルを保存
- `fit`、`citations`、`ignore_links`、`ignore_images`: CLIの `--fit`、`--citations`、`--no-links`、`--no-images` と同じ内容整形オプション(Markdownのみ有効)
- `timeout_s`: この呼び出しでのページ読み込みタイムアウト(秒)。省略時はサーバーの `--timeout` の値
- `directory`(`download` のみ): サーバーのダウンロードディレクトリ(`--download-dir`)からの相対サブディレクトリ。そのディレクトリの外には出られない。省略時はダウンロードディレクトリ自体

複数URLを指定した場合、各結果の先頭に `<!-- crawl4tools: url=... status=... -->` という行が付きます。失敗したURLは代わりに `error: ...` という行で報告され、呼び出し自体が失敗になるのは全URLが失敗したときだけです。プロキシのフォールバックなどの注記は `<!-- note: ... -->` という行で示されます。`download` は保存先に既存のファイルがあれば上書きします。

### オプション

| オプション | 意味 |
|---|---|
| `--transport` | `stdio`(既定)または `http` |
| `--host` | 待ち受けるホスト(httpトランスポートのみ、既定: `127.0.0.1`) |
| `--port` | 待ち受けるポート(httpトランスポートのみ、既定: `8765`) |
| `--path` | MCPエンドポイントのHTTPパス(httpトランスポートのみ、既定: `/mcp`) |
| `--proxy URL` | `http://`、`https://`、`socks5://` のプロキシ。認証情報は `user:pass@host:port` |
| `--no-fallback` | プロキシ障害時に直接通信で再試行しない |
| `--timeout SECONDS` | ツール呼び出しで `timeout_s` を省略した場合に使われる既定のタイムアウト(既定: 60) |
| `-j, --concurrency N` | 全ツール呼び出しを通じて同時に取得するURL数の上限(既定: 3) |
| `--max-urls N` | 1回の呼び出しで受け付けるURL数の上限(既定: 20) |
| `--download-dir DIR` | `download` ツールの保存先ルートディレクトリ(既定: カレントディレクトリ) |
| `--config FILE` | YAMLまたはJSONの設定ファイル(下記「設定」を参照) |
| `-v, --verbose` | 標準エラーへの詳細ログ出力 |

### 設定

設定値は「コマンドラインオプション > `CRAWL4MCP_*` 環境変数 > 設定ファイル > 組み込みの既定値」の優先順位で解決されます。

すべてのオプションは、オプション名を大文字にしてハイフンをアンダースコアに置き換えたものに `CRAWL4MCP_` を付けた環境変数でも指定できます。例えば `--max-urls` には `CRAWL4MCP_MAX_URLS`、`--concurrency` には `CRAWL4MCP_CONCURRENCY` です。`CRAWL4MCP_CONFIG` は `--config` と同様に設定ファイル自体を指定します。

設定ファイルは YAML です(JSON は YAML の一種なので JSON でも動作します)。キーはオプション名のハイフンをアンダースコアに置き換えたもので、未知のキーがあるとエラーになります。

```yaml
transport: http
host: 127.0.0.1
port: 8765
max_urls: 50
concurrency: 5
download_dir: ./downloads
proxy: http://proxy.local:8080
```

設定ファイル内の相対パス(`download_dir` など)は、サーバーを起動したカレントディレクトリを基準に解決されます。

### セキュリティ

Streamable HTTP トランスポートには認証機能がありません。既定ではサーバーは `127.0.0.1` のみで待ち受けますが、他のホストにバインドすると、そのポートに到達できる誰もがサーバーを利用できてしまいます。この場合 `crawl4mcp` は起動時に標準エラーへ警告を出力します。stdioモードでは標準出力はMCPプロトコル専用であり、ログはすべて標準エラーに出力されます。

## 謝辞

This product includes software developed by UncleCode (https://x.com/unclecode) as part of the Crawl4AI project (https://github.com/unclecode/crawl4ai). Crawl4AI は Apache License 2.0 のもとで公開されています。

## ライセンス

本プロジェクトは Apache License 2.0 のもとで公開されています。詳細は [LICENSE](LICENSE) ファイルを参照してください。
