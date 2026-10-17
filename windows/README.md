# Windows版Redeco

[共通の概要・ツール仕様](../README.md) · [Ubuntu版の手順](../ubuntu/README.md)

Ubuntu版と同じリポジトリ・MCPツールを使い、Windows上で直接コマンド実行、ファイル編集、Git、ビルド、長時間処理、対話入力、画面取得を行います。WSLやUbuntu経由のSSHは不要です。Windows 10/11、Python 3.10以降、Windows PowerShell 5.1以降が対象です。

## 実行モードと権限

| 項目 | Windowsでの動作 |
| --- | --- |
| コマンド | `powershell.exe`・`pwsh.exe`・`cmd.exe`・Python・Gitなどを明示して実行 |
| ファイル | 共通のUTF-8読み書き・SHA-256による上書き確認。ルートは`;`区切り、ドライブ文字に対応 |
| 長時間処理 | `start_process`で時間制限なし。出力は最大256KiB保持し、セッションIDで追跡 |
| 対話入力 | `write_process_input`から行単位入力。Windows版はパイプであり、ConPTY・全画面端末・端末のCtrl-Cには未対応 |
| 停止 | Windows Job Objectで子孫をまとめて終了。タイムアウト、MCP終了、監視プロセス強制終了も対象 |
| 画面取得 | ログイン済みデスクトップのスクリーンショット。マウス・キーボード操作ツールは含まない |
| 自動起動 | 現在のWindowsユーザーのログイン時にタスクスケジューラで起動。ログイン前・ログアウト中は動かない |
| 自動復帰 | サーバー異常終了時は5秒後に再起動。監視プロセスの異常終了はタスクスケジューラで再試行 |
| 管理者権限 | 既定は通常権限。`-AllowAdmin`で管理者モードを選択。Windows側の権限は初回設定時に必要 |

コマンドは実行中のWindowsアカウントのOS権限を使います。`-Roots`はファイルツールの対象とコマンドの開始ディレクトリを指定するもので、コマンド全体の隔離ではありません。`.env`等の拒否名はWindowsでは大文字小文字を区別せず、UNCパス・代替データストリームは専用ファイルツールから拒否します。

`-AllowAdmin`は個別コマンドごとの昇格ではなく、MCP全体を管理者権限で動かす選択です。通常モードでのUAC自動承認やUAC無効化は行いません。管理者モードからは保護された認証情報にもアクセス可能になるため、トンネルとの分離を完全な境界とは見なさないでください。

## 新規導入

Git、Pythonと`py`ランチャーを導入してから、普段使うWindowsアカウントのPowerShellで実行します。以降のコマンドはリポジトリのルート（`redeco`）で実行します。READMEがある`windows`ディレクトリへ移動する必要はありません。

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\Sites" | Out-Null
Set-Location "$env:USERPROFILE\Sites"
git clone https://github.com/kkoch5t2/redeco.git
Set-Location redeco
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\install.ps1
```

既定の作業ルートは`$env:USERPROFILE\Sites`、待ち受けは`http://127.0.0.1:8770/mcp`です。外部へポートは公開しません。初回登録がOSのタスク登録ポリシーで拒否される場合は、同じアカウントの管理者PowerShellで導入を再実行します。通常権限のタスクを登録する場合、`-AllowAdmin`は付けません。

| オプション | 用途 |
| --- | --- |
| `-Roots @('C:\Projects','D:\Work')` | 既存のローカルディレクトリを複数指定。PowerShellから`& .\windows\install.ps1`として呼び出す |
| `-Port 8770` | MCPのポート。変更する場合はトンネルの転送先も合わせる |
| `-AllowAdmin` | 初回だけ同じアカウントの管理者PowerShellから設定。次回ログイン以降も選択が維持される |
| `-AllowAdmin:$false` | 管理者モードを解除。PowerShellから`& .\windows\install.ps1 -AllowAdmin:$false`として呼び出す |
| `-NoAutoStart` | 設定と依存関係のみ準備。既存タスクは削除しない |
| `-ConfigureOnly` | 自動起動タスクを登録するが、今回の起動は行わない |

設定、監査、ログはリポジトリ外の`$env:LOCALAPPDATA\Redeco`へ保存し、導入ユーザーとSYSTEMだけにアクセスを許可します。仮想環境は`.venv-windows`に作成します。リポジトリを移動する場合は新しい場所でインストーラーを再実行してください。

## 起動確認と操作例

```powershell
.\.venv-windows\Scripts\python.exe .\windows\check_runtime.py --screenshot
```

`check_runtime.py`は稼働中のMCPへ接続してツール・コマンド・日本語ファイルの往復・画面取得を検証し、作成した一時ファイルを削除します。同じMCPの`run_command`から同期実行せず、Windows端末や独立したプロセスで実行してください。起動ログは`$env:LOCALAPPDATA\Redeco\server.log`です。自動起動を使わず前景で監視プロセスを動かす場合は次を実行します。同じ設定での二重起動はロックで拒否します。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\run.ps1
```

ツールの共通仕様・出力制限は[共通README](../README.md#ツールの使い方)を参照してください。MCPの`run_command`にはシェル文字列ではなく、引数配列を渡します。例中のパスは対象PCの設定ルートへ置き換えます。

```json
{"argv":["powershell.exe","-NoProfile","-NonInteractive","-Command","Get-Date"],"cwd":"C:\\Projects"}
```

長時間処理や入力待ちは`start_process`を使います。PowerShellの継続入力には`-NoProfile -NoLogo -Command -`、cmdには`/d /q`を指定できます。パイプ動作のため、出力をバッファリングするCLIでは明示的なflush設定が必要です。Pythonは`-u`を使います。Windows PowerShell 5.1の日本語出力には`[Console]::OutputEncoding=[Text.UTF8Encoding]::new()`を指定します。

`.bat`・`.cmd`は暗黙のシェル起動を避け、`cmd.exe /d /s /c`を明示します。セッションの出力はUTF-8として扱います。UTF-8非対応の外部ツールでは文字化けする場合があります。コマンドの環境からトンネルキー等は除外し、PATH、USERPROFILE、SystemRoot、TEMPやSDK環境を引き継ぎます。

## ChatGPTへの接続

Windows MCPのローカル起動と、ChatGPTで使うトンネル・接続の作成は別工程です。Windows PC専用のトンネルを用意します。既存Ubuntu PCのトンネルIDを共有しないでください。

1. [OpenAI PlatformのTunnels設定](https://platform.openai.com/settings/organization/tunnels)でトンネルと実行用キーを作ります。
2. 同画面または[公式リリース](https://github.com/openai/tunnel-client/releases/latest)からWindows向け`tunnel-client.exe`を取得します。
3. `tunnel-client.exe help quickstart`で現在の設定手順を確認し、HTTP MCP転送先を`http://127.0.0.1:8770/mcp`に設定します。公式CLIでは`init --mcp-server-url`を使えます。
4. トンネル専用Windowsアカウントで設定・実行し、キーとプロファイルのACLをそのアカウントに限定します。MCPのconfig.json、Git、コマンド引数、チャットにはキーを入れません。
5. `doctor`と`run`で接続を確認します。継続運用にはその専用アカウントで起動するタスクまたはサービスを別途設定します。本インストーラーはトンネルキーの保存やトンネル常駐タスクを作成しません。
6. [ChatGPTのPlugins設定](https://chatgpt.com/#settings/Connectors)でカスタムMCPを追加し、名前を`redeco-windows`等にして、このトンネルを選びます。MCPのAuthenticationは`No authentication`です。

詳細は[公式Secure MCP Tunnel手順](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)を参照してください。MCP自体は認証を実装しておらず、同じPC上でループバックへ到達できるプロセスも呼び出せます。トンネルキーをMCPと同じWindowsアカウントへ保存すると、そのアカウントのコマンドから読み取れるため、分離した構成と説明しないでください。

## 導入済み環境の更新

実行中セッションを完了させてから、同じ作業コピーで更新します。

```powershell
git pull --ff-only
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\install.ps1
```

既存のルート、ポート、管理者モードを引き継いでタスクを再登録し、MCPを再起動します。管理者モードを維持する場合は、同じアカウントの管理者PowerShellから実行します。再起動でセッションと保持出力は失われます。

## 停止・削除

自動起動を解除する場合は次を実行します。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\uninstall.ps1
```

現在のユーザーのRedecoタスクだけを停止・削除します。設定・監査・ソース・仮想環境は残します。トンネルは別の設定のため、削除・失効も別途行ってください。

## 運用・トラブルシューティング

| 症状 | 確認すること |
| --- | --- |
| ローカルMCPへ接続できない | ログイン状態、Redecoタスクの状態、`server.log`、設定したポート |
| ローカルMCPは使えるがChatGPTから接続できない | Windows専用トンネルの起動状態とChatGPTへの登録 |
| 管理者権限が必要な操作に失敗する | `host_status`の`runtime.allow_admin`と`-AllowAdmin`の設定 |
| 日本語が文字化けする | CLIの出力文字コード。セッションはUTF-8として解釈 |
| セッションIDが見つからない | MCP再起動や保持上限による削除。実行済みか確認してから再開 |
| 長いコマンドがタイムアウトする | `start_process`を使い、IDで追跡 |

| 保存先 | 内容 |
| --- | --- |
| リポジトリ内の`.venv-windows` | Windows用のPython仮想環境 |
| `$env:LOCALAPPDATA\Redeco\config.json` | 実行ルート・ポート・管理者モード・リポジトリの場所 |
| `$env:LOCALAPPDATA\Redeco\audit.jsonl` | ツールの監査ログ |
| `$env:LOCALAPPDATA\Redeco\server.log` | 監視プロセスとMCPの起動ログ。1MiBごとにローテーションし、過去3世代を保持 |

## 開発・検証

依存関係は新規導入のインストーラーで準備します。リポジトリのルートで実行します。

```powershell
.\.venv-windows\Scripts\python.exe -m unittest test_windows -v
```

125秒の連続実行も検証する場合は、このPowerShellセッションだけで環境変数を設定します。

```powershell
$env:REDECO_TEST_LONG = '1'
try {
    .\.venv-windows\Scripts\python.exe -m unittest test_windows -v
} finally {
    Remove-Item Env:REDECO_TEST_LONG
}
```

`test_windows.py`は一時ディレクトリとstdioのMCPサーバーを使って検証します。稼働中のHTTPサーバーを検証する`windows/check_runtime.py`とは用途を分けています。PowerShellスクリプトの構文確認と、Ubuntuを含む検証の定義は[CI設定](../.github/workflows/tests.yml)を参照してください。
