# crawl4tools

[English README is here](README.md)

## crawl4tools とは

crawl4tools は [crawl4ai](https://github.com/unclecode/crawl4ai) ライブラリを基盤にしたWebクローラーです。次の3つの用途で使えるように設計されています。

1. **Open WebUI の external Web loader** として直接使えるHTTPサーバー `crawl4server`。Open WebUI の Web検索で取得したURLの本文をMarkdownで返します。Open WebUI 側は管理画面で Web Loader Engine を `external` に、External Web Loader URL を設定するだけで利用できます(同等の環境変数は `WEB_LOADER_ENGINE=external` と `EXTERNAL_WEB_LOADER_URL`)。
2. **MCPサーバー**。Claude Code などのAIエージェントから、要約ではなく**全文**を取得できるWeb fetchツールとして機能します。
3. **ローカルCLI**。指定したURL(複数のURLを一度に指定することもできます)をMarkdown等の形式でダウンロードします。

## 現状

**Beta。** このリリース(1.0.0b3)は crawl4tools のベータ版で、[PyPI](https://pypi.org/project/crawl4tools/) と [Docker Hub](https://hub.docker.com/r/xhighhongo41/crawl4tools) で公開されています。ローカルCLI `crawl4cli`、MCPサーバー `crawl4mcp`、Open WebUI Web loader と MCP を兼ねる `crawl4server` が使え、Dockerfile と compose ファイルも用意されています。メッセージは英語・日本語のどちらでも表示できます。実際に使ってみたフィードバックを歓迎します([Issuesページ](https://github.com/xhighhongo41/crawl4tools/issues)へどうぞ)。このベータ版でのテストを経て、1.0.0 の正式版を予定しています。

## 機能

利用できるもの(CLI):

- 1つまたは複数のURLを Markdown、HTML、PDF、スクリーンショット(PNG)、MHTML、元データのままの形式でダウンロード
- PDF は Markdown に文字起こし。画像などHTML以外のファイルはそのまま保存
- HTTPエラー、ホスト名の解決失敗、接続拒否、タイムアウト、ブラウザ未導入を区別した分かりやすいエラーメッセージ
- HTTP/HTTPS/SOCKS5 プロキシ経由のダウンロード。プロキシ自体の障害や、結果を壊す場合は直接通信で1回だけ再試行 ── TLS を傍受する(「SSL bump」)プロキシで、TLSエラー、プロキシ自身が生成したエラーページ、プロキシ越しに見えるボット判定のいずれかが起きた場合を含む。プロキシ自体による拒否は迂回しない
- メッセージは英語・日本語のどちらでも表示可能([メッセージの言語](#メッセージの言語)参照)

利用できるもの(MCPサーバー):

- 2つのツール: `fetch` はページを Markdown(既定)、HTML、PNGスクリーンショットとしてそのままクライアントに返す(画像は画像として、PDFは Markdown に文字起こしして返る)。`download` は Markdown、HTML、PDF、スクリーンショット、MHTML、元データのいずれかの形式でサーバー上のディレクトリに保存し、保存先パスを返す
- 1回の呼び出しで複数URL(既定の上限20件)を指定でき、サーバー全体の同時実行数の上限(既定3)のもとで並行して取得。ヘッドレスブラウザは1つを共有
- stdio(既定)と Streamable HTTP の両トランスポートに対応
- サーバー側で設定するプロキシと直接通信へのフォールバック(TLSを傍受するプロキシにも対応)
- 設定はコマンドラインオプション、`CRAWL4MCP_*` 環境変数、YAML/JSON設定ファイルのいずれでも可能
- メッセージは英語・日本語のどちらでも表示可能([メッセージの言語](#メッセージの言語)参照)

利用できるもの(Open WebUI Web loader、`crawl4server`):

- `POST /crawl` は `{"urls": [...]}` を受け取り、Open WebUI が利用できるMarkdownとメタデータ(source、URL、title、ステータスコード、content type)をページごとに返す。無効なURLや取得失敗したURLはバッチ全体を失敗させず、単に応答から除かれる
- `crawl4mcp` と同じMCPエンドポイントを別ポートで提供し、Web loaderとヘッドレスブラウザ1つ・同時実行数の上限を共有
- 死活監視用の `GET /health` と、Web loaderエンドポイント向けの任意のbearer APIキー
- 設定はコマンドラインオプション、`CRAWL4SERVER_*` 環境変数、YAML/JSON設定ファイルのいずれでも可能
- コンテナで動かすための Dockerfile と compose ファイル
- メッセージは英語・日本語のどちらでも表示可能([メッセージの言語](#メッセージの言語)参照)

予定:

- MCPエンドポイントの認証

## インストール

CLI と MCPサーバーには Python 3.11 以上と [uv](https://docs.astral.sh/uv/) が必要です。

```sh
uv tool install --with-executables-from playwright crawl4tools
playwright install chromium   # ヘッドレスブラウザをダウンロード(初回のみ)
```

これで [PyPI](https://pypi.org/project/crawl4tools/) から `crawl4cli`、`crawl4mcp`、`crawl4server` がインストールされます。

代わりに開発版をインストールするには、`uv` の指定先をgitリポジトリにします。

```sh
uv tool install --with-executables-from playwright git+https://github.com/xhighhongo41/crawl4tools
```

ブラウザは Playwright のキャッシュディレクトリ(macOS なら `~/Library/Caches/ms-playwright` など)に保存されます。また crawl4ai が自身のデータ用に `~/.crawl4ai` ディレクトリを作成します。

`crawl4server` をコンテナで動かす場合は、後述の[Docker](#docker)を参照してください。

## 使い方

```sh
crawl4cli https://example.com/                    # Markdown を標準出力へ
crawl4cli -o page.md https://example.com/         # ファイルに保存
crawl4cli -d out/ URL1 URL2 URL3                  # 複数URLをディレクトリへ
crawl4cli -f screenshot https://example.com/      # example.com.png を保存
crawl4cli --proxy http://proxy.local:8080 URL     # プロキシ経由
```

すべてのオプションは、オプション名を大文字にしてハイフンをアンダースコアに置き換えたものに `CRAWL4CLI_` を付けた環境変数でも指定できます。オプションを指定した場合は環境変数より優先されます。標準の `HTTP_PROXY`/`HTTPS_PROXY` 環境変数は使いません。

| オプション | 環境変数 | 既定値 | 意味 |
|---|---|---|---|
| `-f, --format` | `CRAWL4CLI_FORMAT` | `markdown` | `markdown`、`html`、`pdf`、`screenshot`、`mhtml`、`raw` |
| `-o, --output FILE` | `CRAWL4CLI_OUTPUT` | *(なし。標準出力に出力)* | 単一URLを標準出力ではなく FILE に保存 |
| `-d, --output-dir DIR` | `CRAWL4CLI_OUTPUT_DIR` | `.` | 複数URLとバイナリ形式の保存先 |
| `--proxy URL` | `CRAWL4CLI_PROXY` | *(なし)* | `http://`、`https://`、`socks5://` のプロキシ。認証情報は `user:pass@host:port` |
| `--fallback/--no-fallback` | `CRAWL4CLI_FALLBACK` | `--fallback`(有効) | プロキシ自体に問題があると見られる場合は、直接通信で再試行する |
| `-j, --concurrency N` | `CRAWL4CLI_CONCURRENCY` | `3` | 同時に取得するURL数 |
| `--timeout SECONDS` | `CRAWL4CLI_TIMEOUT` | `60` | URLごとのページ読み込みタイムアウト |
| `--citations` | `CRAWL4CLI_CITATIONS` | 無効 | リンクを番号付き参照にし、末尾に一覧を付ける |
| `--fit` | `CRAWL4CLI_FIT` | 無効 | 本文だけを残す(メニューやフッターなどを除く)。何も残らなかった場合はページ全体を出力 |
| `--no-links` | `CRAWL4CLI_NO_LINKS` | 無効 | Markdown からリンクを除く |
| `--no-images` | `CRAWL4CLI_NO_IMAGES` | 無効 | Markdown から画像参照を除く |
| `-q, --quiet` | `CRAWL4CLI_QUIET` | 無効 | 標準エラーへの出力を減らす |
| `-v, --verbose` | `CRAWL4CLI_VERBOSE` | 無効 | 標準エラーへの出力を増やす |
| `--lang en\|ja` | `CRAWL4CLI_LANG` | OSのロケールに従う | メッセージの言語([メッセージの言語](#メッセージの言語)を参照) |

標準出力に出るのは本文だけで、通知・エラー・要約は標準エラーに出ます。ファイル名はURLから作られます(`https://example.com/a/b` → `example.com_a_b.md`)。終了コードは、全URL成功で 0、1件でも失敗で 1、引数の誤りで 2 です。

## Open WebUI Web loader(crawl4server)

`crawl4server` は1つのプロセスで Open WebUI の external Web loader と MCPサーバーの両方を、それぞれ別のポートで提供します。ヘッドレスブラウザ1つと同時実行数の上限(`-j`)を両者で共有します。

### 起動

```sh
crawl4server
```

既定では、Web loader(`POST /crawl`、`GET /health`)は `127.0.0.1:8766`、MCP(`/mcp`)は `127.0.0.1:8765` で待ち受けます。

### Open WebUI との接続

Open WebUI(0.11.x 以降)の管理画面 Admin Settings > Web Search で、次のように設定します。

- **Web Loader Engine**: `external`
- **External Web Loader URL**: `http://<host>:8766/crawl`
- **External Web Loader API Key**: `--loader-api-key` に渡した値(未設定なら空欄のまま)

Open WebUI 側の同等の環境変数は `WEB_LOADER_ENGINE=external`、`EXTERNAL_WEB_LOADER_URL`、`EXTERNAL_WEB_LOADER_API_KEY` です。

Open WebUI はこのURLに `{"urls": [...]}` をPOSTし、`{"page_content": <Markdown>, "metadata": {"source", "url", "title", "status_code", "content_type"}}` の形式のドキュメントの配列を受け取ります。無効なURLや取得に失敗したURLは応答から単に除かれ(サーバーの標準エラーにログが出ます)、1件のURLの不具合がバッチ全体を失敗させることはありません。Open WebUI はこのリクエストをタイムアウトさせないため、検索が遅く感じる場合は `--timeout` と `-j`/`--concurrency` を調整してください。

### オプション

設定値は「コマンドラインオプション > `CRAWL4SERVER_*` 環境変数(オプション名を大文字にしてハイフンをアンダースコアに置き換えたもの。例: `CRAWL4SERVER_LOADER_API_KEY`) > 設定ファイル(下記の設定キー列。[設定](#設定)を参照) > 組み込みの既定値」の優先順位で解決されます。

| オプション | 環境変数 | 設定キー | 既定値 | 意味 |
|---|---|---|---|---|
| `--host` | `CRAWL4SERVER_HOST` | `host` | `127.0.0.1` | 待ち受けるホスト、両ポート共通 |
| `--loader-port` | `CRAWL4SERVER_LOADER_PORT` | `loader_port` | `8766` | Web loaderのポート |
| `--loader-path` | `CRAWL4SERVER_LOADER_PATH` | `loader_path` | `/crawl` | Web loaderエンドポイントのHTTPパス |
| `--loader-api-key KEY` | `CRAWL4SERVER_LOADER_API_KEY` | `loader_api_key` | *(なし)* | Web loaderで `Authorization: Bearer KEY` を必須にする(Open WebUI の External Web Loader API Key) |
| `--loader-fit/--no-loader-fit` | `CRAWL4SERVER_LOADER_FIT` | `loader_fit` | `--no-loader-fit`(無効) | 各ページの本文だけを残す。無効時はページ全体をMarkdown化 |
| `--mcp-port` | `CRAWL4SERVER_MCP_PORT` | `mcp_port` | `8765` | MCP Streamable HTTPのポート |
| `--mcp-path` | `CRAWL4SERVER_MCP_PATH` | `mcp_path` | `/mcp` | MCPエンドポイントのHTTPパス |
| `--proxy URL` | `CRAWL4SERVER_PROXY` | `proxy` | *(なし)* | `http://`、`https://`、`socks5://` のプロキシ。認証情報は `user:pass@host:port` |
| `--fallback/--no-fallback` | `CRAWL4SERVER_FALLBACK` | `fallback` | `--fallback`(有効) | プロキシ自体に問題があると見られる場合は、直接通信で再試行する |
| `--timeout SECONDS` | `CRAWL4SERVER_TIMEOUT` | `timeout` | `60` | Web loaderのリクエストおよび `timeout_s` を省略したMCPツール呼び出しで使われる既定のタイムアウト |
| `-j, --concurrency N` | `CRAWL4SERVER_CONCURRENCY` | `concurrency` | `3` | 両ポートを通じて同時に取得するURL数の上限 |
| `--max-urls N` | `CRAWL4SERVER_MAX_URLS` | `max_urls` | `20` | 1回のWeb loaderリクエストまたはMCPツール呼び出しで受け付けるURL数の上限。Open WebUI は最大20件を送るため、20以上に保つ |
| `--download-dir DIR` | `CRAWL4SERVER_DOWNLOAD_DIR` | `download_dir` | `.` | MCPの `download` ツールの保存先ルートディレクトリ |
| `-v, --verbose` | `CRAWL4SERVER_VERBOSE` | `verbose` | 無効 | 標準エラーへの詳細ログ出力 |
| `--lang en\|ja` | `CRAWL4SERVER_LANG` | `lang` | `en` | サーバーが動作中に出力するメッセージの言語([メッセージの言語](#メッセージの言語)を参照) |
| `--config FILE` | `CRAWL4SERVER_CONFIG` | — | *(なし)* | YAMLまたはJSONの設定ファイル(下記の[設定](#設定)を参照) |

### 設定

設定ファイルは任意のパスに置ける YAML または JSON で、`--config` または `CRAWL4SERVER_CONFIG` で指定します。キーは上表の設定キー列と同じで、それ以外のキーがあるとエラーになります。相対パスの値(`download_dir` など)は、サーバーを起動したディレクトリを基準に解決されます。

```yaml
host: 0.0.0.0
loader_port: 8766
loader_api_key: change-me
mcp_port: 8765
max_urls: 20
concurrency: 3
lang: ja
```

同じ内容をJSONで書くと:

```json
{
  "host": "0.0.0.0",
  "loader_port": 8766,
  "loader_api_key": "change-me",
  "mcp_port": 8765,
  "max_urls": 20,
  "concurrency": 3,
  "lang": "ja"
}
```

### Docker

`compose.yaml` は [Docker Hub](https://hub.docker.com/r/xhighhongo41/crawl4tools) で公開されているイメージ(`xhighhongo41/crawl4tools`、linux/amd64・linux/arm64 に対応)を取得します。

```sh
git clone https://github.com/xhighhongo41/crawl4tools
cd crawl4tools
mkdir -p downloads
docker compose up -d
curl http://localhost:8766/health
```

compose を使わずに同じイメージを直接取得することもできます: `docker pull xhighhongo41/crawl4tools:1.0.0b3`。ベータ版には `latest` タグが付かないため、必ずバージョンタグを指定してください。取得せずローカルでビルドする場合は、`docker build -t xhighhongo41/crawl4tools:1.0.0b3 .` を実行してから `docker compose up -d` してください。

compose ファイルそのもの(コメントは英語のままです):

<!-- compose.yaml -->
```yaml
# crawl4tools server (crawl4server): Open WebUI external web loader + MCP.
#
# The image is pulled from Docker Hub; `docker compose up -d` fetches
# xhighhongo41/crawl4tools:1.0.0b3. To build locally instead, run
# `docker build -t xhighhongo41/crawl4tools:1.0.0b3 .` first.
#
# Open WebUI: Admin Settings > Web Search > Web Loader Engine = "external",
# URL = http://<host>:8766/crawl (or http://crawl4tools:8766/crawl when
# Open WebUI runs in this same compose project), API key = the value set
# for CRAWL4SERVER_LOADER_API_KEY below.
#
# MCP (Streamable HTTP): http://<host>:8765/mcp — this endpoint has no
# authentication, so do not publish port 8765 beyond a trusted network.
#
# Before first run: mkdir -p downloads (must be writable by uid 1000, the
# "crawl" user the container runs as).

services:
  crawl4tools:
    image: xhighhongo41/crawl4tools:1.0.0b3
    ports:
      - "8766:8766"
      - "8765:8765"
    environment:
      # Every crawl4server CLI option also reads an env var named
      # CRAWL4SERVER_<OPTION_UPPER_SNAKE>; CRAWL4SERVER_HOST below mirrors
      # the --host already passed in the Dockerfile's CMD, kept here as a
      # harmless, real example of the naming scheme.
      CRAWL4SERVER_HOST: "0.0.0.0"
      # CRAWL4SERVER_LOADER_API_KEY: change-me
      # CRAWL4SERVER_PROXY: socks5://host:1080
      # CRAWL4SERVER_CONCURRENCY: "3"
      # CRAWL4SERVER_MAX_URLS: "20"
      # CRAWL4SERVER_TIMEOUT: "60"
      # CRAWL4SERVER_LANG: ja
    volumes:
      # Host directory for downloaded/converted output; create it first
      # (mkdir -p downloads) and make sure uid 1000 can write to it.
      - ./downloads:/data/downloads
    # Chromium needs more than Docker's default 64m /dev/shm to avoid
    # crashing on larger pages.
    shm_size: "1gb"
    restart: unless-stopped
```

- `image`: このリリースのタグが付いた公開Dockerイメージ(`xhighhongo41/crawl4tools:1.0.0b3`)。ローカルビルドを使う場合は、上記の `docker build` コマンドを先に実行してから `docker compose up -d` してください。
- `ports`: `8766` は Open WebUI Web loader、`8765` は MCP。どちらか一方だけを公開するには、不要な行を削除するか(あるいはネットワークに一切出さないよう `"127.0.0.1:8765:8765"` のように `127.0.0.1` にバインドしてください)。
- `environment`: 有効にしたい行のコメントを外してください。Open WebUI の External Web Loader API Key に設定した値を `CRAWL4SERVER_LOADER_API_KEY` に設定してください。他の `CRAWL4SERVER_*` 変数は上記のオプション表を参照してください。
- `volumes`: 先にホスト側の `downloads/` ディレクトリを作成し(上記の `mkdir -p downloads`)、コンテナ実行ユーザーである uid 1000 が書き込めるようにしてください。
- `shm_size`: Chromium が大きなページでクラッシュしないよう、Docker の既定である64MBより大きい `/dev/shm` が必要です。
- `restart`: `unless-stopped` は、クラッシュやホストの再起動後にコンテナを再起動しますが、明示的な `docker compose down` の後は再起動しません。

Open WebUI が同じ compose プロジェクトで動いている場合は、`localhost` の代わりに `http://crawl4tools:8766/crawl` を指定してください。コンテナを停止する際(`docker stop` または `docker compose down`)は、処理中のリクエストを最大5秒待ってから残りの接続を閉じます。

### セキュリティ

Web loader は `--loader-api-key` を設定した場合のみAPIキーを検証します。MCPポートには認証機能が一切ありません。ループバック以外のホストで待ち受ける場合(Dockerでの構成など)は、loader APIキーを設定し、MCPポートを信頼できるネットワークの外に公開しないでください。その場合 `crawl4server` は起動時に標準エラーへ警告を出力します。`crawl4server` はHTTPのみで動作します。HTTPSが必要な場合は、手前にリバースプロキシを置いてTLSを終端してください。

## MCPサーバー

`crawl4mcp` は同じ取得エンジンを [MCP](https://modelcontextprotocol.io/) サーバーとして公開し、`fetch`(内容を直接返す)と `download`(ファイルに保存する)の2つのツールを提供します。`crawl4server`(前述)は、この同じMCPエンドポイントを Open WebUI Web loader と同じプロセスから提供します。

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

Claude Desktop の場合は、設定ファイル(macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`、Windows: `%APPDATA%\Claude\claude_desktop_config.json`)を開き、`mcpServers` の中に項目を1つ追加します(他のサーバーが既にある場合は、カンマで区切って別のキーとして追記してください)。保存後は Claude Desktop を再起動してください。

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

HTTP(`--transport http` または `crawl4server`)経由の場合、`download` が保存した各ファイルには `file_url` も付き、同じポートの `/files/<token>` で配信されます。例えば `curl -o <name> <file_url>` で自分のマシンに保存でき、内容が会話を経由することはありません。stdio の場合はサーバーがクライアントと同じマシンで動くため、返されたパスをそのまま使えます。`file_url` はサーバーを再起動すると無効になります(トークンが失われるため)。ただしファイル自体は残ります。

`fetch` の構造化結果では、ページ本文が `pages[i].text` にも入っており、`content` ブロックの同じ本文と重複しています。`structuredContent` の方を見るクライアント(Claude Code はそうします)でも本文を取得できます。Claude Code は MCP の結果が 25,000 トークン(`MAX_MCP_OUTPUT_TOKENS`)を超えるとファイルに退避するため、長いページでは `download` を使うとこのやり取りを避けられます。

### オプション

設定値は「コマンドラインオプション > `CRAWL4MCP_*` 環境変数(オプション名を大文字にしてハイフンをアンダースコアに置き換えたもの。例: `CRAWL4MCP_MAX_URLS`) > 設定ファイル(下記の設定キー列。[設定](#設定-1)を参照) > 組み込みの既定値」の優先順位で解決されます。

| オプション | 環境変数 | 設定キー | 既定値 | 意味 |
|---|---|---|---|---|
| `--transport` | `CRAWL4MCP_TRANSPORT` | `transport` | `stdio` | `stdio` または `http` |
| `--host` | `CRAWL4MCP_HOST` | `host` | `127.0.0.1` | 待ち受けるホスト(httpトランスポートのみ) |
| `--port` | `CRAWL4MCP_PORT` | `port` | `8765` | 待ち受けるポート(httpトランスポートのみ) |
| `--path` | `CRAWL4MCP_PATH` | `path` | `/mcp` | MCPエンドポイントのHTTPパス(httpトランスポートのみ) |
| `--proxy URL` | `CRAWL4MCP_PROXY` | `proxy` | *(なし)* | `http://`、`https://`、`socks5://` のプロキシ。認証情報は `user:pass@host:port` |
| `--fallback/--no-fallback` | `CRAWL4MCP_FALLBACK` | `fallback` | `--fallback`(有効) | プロキシ自体に問題があると見られる場合は、直接通信で再試行する |
| `--timeout SECONDS` | `CRAWL4MCP_TIMEOUT` | `timeout` | `60` | ツール呼び出しで `timeout_s` を省略した場合に使われる既定のタイムアウト |
| `-j, --concurrency N` | `CRAWL4MCP_CONCURRENCY` | `concurrency` | `3` | 全ツール呼び出しを通じて同時に取得するURL数の上限 |
| `--max-urls N` | `CRAWL4MCP_MAX_URLS` | `max_urls` | `20` | 1回の呼び出しで受け付けるURL数の上限 |
| `--download-dir DIR` | `CRAWL4MCP_DOWNLOAD_DIR` | `download_dir` | `.` | `download` ツールの保存先ルートディレクトリ |
| `-v, --verbose` | `CRAWL4MCP_VERBOSE` | `verbose` | 無効 | 標準エラーへの詳細ログ出力 |
| `--lang en\|ja` | `CRAWL4MCP_LANG` | `lang` | `en` | サーバーが動作中に出力するメッセージの言語([メッセージの言語](#メッセージの言語)を参照) |
| `--config FILE` | `CRAWL4MCP_CONFIG` | — | *(なし)* | YAMLまたはJSONの設定ファイル(下記の[設定](#設定-1)を参照) |

### 設定

設定ファイルは任意のパスに置ける YAML または JSON で、`--config` または `CRAWL4MCP_CONFIG` で指定します。キーは上表の設定キー列と同じで、それ以外のキーがあるとエラーになります。相対パスの値(`download_dir` など)は、サーバーを起動したディレクトリを基準に解決されます。

```yaml
transport: http
host: 127.0.0.1
port: 8765
max_urls: 50
concurrency: 5
download_dir: ./downloads
proxy: http://proxy.local:8080
lang: ja
```

同じ内容をJSONで書くと:

```json
{
  "transport": "http",
  "host": "127.0.0.1",
  "port": 8765,
  "max_urls": 50,
  "concurrency": 5,
  "download_dir": "./downloads",
  "proxy": "http://proxy.local:8080",
  "lang": "ja"
}
```

### セキュリティ

Streamable HTTP トランスポートには認証機能がありません。既定ではサーバーは `127.0.0.1` のみで待ち受けますが、他のホストにバインドすると、そのポートに到達できる誰もがサーバーを利用できてしまいます。この場合 `crawl4mcp` は起動時に標準エラーへ警告を出力します。stdioモードでは標準出力はMCPプロトコル専用であり、ログはすべて標準エラーに出力されます。

`/files/<token>`(前述の[ツール](#ツール)を参照)は、`download` が保存したファイルを MCP と同じポートで配信します。トークンは推測できない乱数ですが、ポート自体には認証機能がないため、MCPポートを信頼できるネットワークの外に公開しないでください。

## メッセージの言語

3つのコマンドはいずれも、メッセージを英語(`en`)または日本語(`ja`)で表示できます。

言語は `--lang` オプション、環境変数(`CRAWL4CLI_LANG`、`CRAWL4MCP_LANG`、`CRAWL4SERVER_LANG`)、または2つのサーバーについては設定ファイルの `lang` キーで指定できます。複数指定された場合は、オプションが最優先、次に環境変数、最後に設定ファイルの順で解決されます。

既定値: `crawl4cli` はOSのロケール(`LANGUAGE`、`LC_ALL`、`LC_MESSAGES`、`LANG` の順に最初に設定されているもの)に従い、値が `ja` で始まる場合(例: `LANG=ja_JP.UTF-8`)は日本語、それ以外は英語になります。`crawl4mcp` と `crawl4server` はロケールの影響を受けず、常に既定で英語になります。これにより、コンテナやMCPクライアントは安定した出力を得られます。

```sh
crawl4cli --lang ja https://example.com/
CRAWL4SERVER_LANG=ja crawl4server
```

翻訳対象は `--help` の内容、エラーメッセージ、標準エラーへの通知や進捗行、MCPツールの説明文と結果テキスト、サーバーの起動時の表示・警告、Web loaderのJSONエラー応答です。`--help` は常に `--lang` または環境変数(`crawl4cli` ではロケールも)に従います。設定ファイルの `lang` はサーバー動作中に出力されるメッセージにのみ適用されます。

常に英語のまま(`--lang` の影響を受けないもの): ログ出力、`error:`・`note:`・`saved:`・`done:`・`failed:` という行頭の接頭辞、JSONのキー、`<!-- crawl4tools: url=... status=... -->` というヘッダー行、`GET /health`、`--version`、click や他のライブラリが出力するもの(例: `Usage:` や `Error: Invalid value ...`)。

1つのプロセスでは1つの言語のみを使用し、リクエストごとに言語を切り替えることはできません。

## 謝辞

This product includes software developed by UncleCode (https://x.com/unclecode) as part of the Crawl4AI project (https://github.com/unclecode/crawl4ai). Crawl4AI は Apache License 2.0 のもとで公開されています。

## ライセンス

本プロジェクトは Apache License 2.0 のもとで公開されています。詳細は [LICENSE](LICENSE) ファイルを参照してください。
