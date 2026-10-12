# redeco

個人のUbuntuホストで動くMCPサーバー。OpenAI Secure MCP Tunnelを使い、ChatGPTからプライベートなMCPへ接続するための試験実装です。MCP本体は `127.0.0.1:8770`、トンネルの管理画面は `127.0.0.1:8780` で待ち受けます。ルーターのポート開放は不要です。

## 権限の境界

- MCPは専用OSユーザー `redeco` で動き、書き込み先は `/var/lib/redeco/workspaces` に限定します。
- トンネルは別のOSユーザー `redeco-tunnel` で動きます。Platformの実行用APIキーは `/etc/redeco/tunnel.env` にroot所有・モード600で保存し、トンネルのプロセス環境にだけ渡します。
- ChatGPTのカスタムMCPは個人ワークスペースに関連付けたTunnelを使い、現状は「No authentication」です。ローカルMCPポートにも独自の認証はありません。ほかのローカルプロセスからのアクセスには注意してください。
- `run_command` は任意のプログラムをMCPユーザー権限で実行します。ファイルツールのパス制約と出力の簡易伏字は、コマンド実行には適用できません。信頼しない入力を扱うときはChatGPTのツール呼び出しを確認してください。

## ツール

`host_status`, `list_files`, `read_file`, `write_file`, `git_status`, `run_command`, `screenshot`。
`screenshot` はGUIセッションと撮影ツールが利用できる場合のみ動きます。専用OSユーザーには普段のデスクトップ権限を与えていません。

初期状態では個人の開発リポジトリ、GitHub認証情報、デプロイ資格情報にはアクセスできません。必要になった場合は用途ごとに権限を設計してください。

## 新規導入（Ubuntu 24.04）

1. このリポジトリをクローンし、`sudo bash ./install_isolated.sh` でMCPを専用ユーザーとして導入します。Python仮想環境は `/opt/redeco/venv` に作成します。
2. [OpenAI公式のtunnel-client](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)を取得し、PlatformでTunnelを作って個人ChatGPTワークスペースに関連付けます。実行用APIキーはTunnelsのRead + Useに限定します。
3. Ubuntuのローカル端末で `sudo bash ./prepare_isolated_tunnel.sh` を実行し、Tunnel IDと実行用APIキーを対話入力します。スクリプトはトンネルを起動しません。公式クライアントが標準の場所にない場合は、sudo実行時に `TUNNEL_CLIENT_BIN` で絶対パスを指定してください。
4. `sudo systemctl enable --now redeco-tunnel-isolated.service` で起動します。ChatGPTのWeb版PluginsでカスタムMCPを作り、ConnectionをTunnel、AuthenticationをNo authenticationにして接続します。
5. `.venv/bin/python check_isolated.py` でMCPの権限境界を確認し、ChatGPTで `host_status` を試します。外部回線のスマートフォンでも実機確認してください。

APIキーはGitやチャットに貼らないでください。トンネル単体の追加料金の有無は公開料金表から確定できていません。APIモデル利用はChatGPT Plusとは別課金です。

## 旧構成からの移行

以前の版でMCPとトンネルを同じ `redeco` ユーザーとして設定した場合は、Ubuntuのローカル端末で `sudo bash ./migrate_tunnel_user.sh` を実行します。既存のキーを画面に表示せず、トンネルの設定とサービスを `redeco-tunnel` に移し、稼働と自動起動状態を確認します。途中で失敗した場合は、同じユーザーのままトンネルを再起動せずに診断してください。

旧 `8769` 番ポートの試験サービスは認証情報に近い権限で動く構成だったため、無効化してください。このリポジトリにはその設定を含めていません。

## 開発・検証

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python smoke_test.py
```

`check_isolated.py` は稼働中のローカルMCPに接続し、トンネルとMCPのプロセス権限が分離されていることを、秘密値を読み出さずに検証します。

## 公開・運用上の注意

コードの公開だけでは個人のTunnelへのアクセス権は渡りません。Tunnel IDやAPIキー、端末固有の設定ファイルをコミットしないでください。実際に導入する場合は自分のTunnelと実行用キーを用意してください。
