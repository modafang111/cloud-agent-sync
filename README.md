# cloud-agent-sync

`D:\dev` 配下にある **複数の既存 Git プロジェクト** を、安全に一括 `fetch` / `commit` / `push` するための管理リポジトリです。

このリポジトリは、各プロジェクトのソースコード置き場ではありません。  
`D:\dev` 全体を 1 つの巨大な Git リポジトリにもしません。

## 設計

```
各開発プロジェクト  →  そのプロジェクト自身の GitHub リポジトリ  →  Cursor Cloud Agent
D:\dev 配下の複数プロジェクト  →  cloud-agent-sync（このツール）  →  一括 pull / commit / push
```

- Cloud Agent が編集するのは、主にこの管理プログラムです。
- 各アプリの同期は、**そのプロジェクト自身の GitHub リポジトリ** 経由で行います。
- 既存プロジェクトの `.git` / `remote` / `branch` は、remote が無いプロジェクトを `sync --provision` で新規接続する場合を除き変更しません。
- `git push --force` / `git reset --hard` / `git clean -fd` / 履歴の強制書き換えは行いません。

日常操作は **`sync` だけ** です。任意で Windows タスクに毎日の `sync` を登録できます。常駐監視はありません。

## インストール方法

前提:

- Windows
- Git for Windows（既存の GitHub 認証をそのまま使います）
- Python 3（`py -3` または `python`）

1. このリポジトリを clone します。置き場所は `D:\dev\cloud-agent-sync` を推奨します。

```powershell
cd D:\dev
git clone https://github.com/modafang111/cloud-agent-sync.git
cd cloud-agent-sync
```

2. インストーラを実行します。ユーザー PATH の **末尾に追加するだけ** で、既存 PATH や PowerShell Profile は書き換えません。

```powershell
.\install.cmd
```

日本語 Windows の Windows PowerShell 5.1 では、UTF-8 の `.ps1` が文字化けして構文エラーになることがあります。このリポジトリの `install.ps1` は UTF-8 BOM 付きです。もし古い版でエラーになったら、最新を `git pull` してから `.\install.cmd` を再実行してください。PowerShell Profile は変更しません。

3. **Cursor とターミナルを再起動** します。
4. 動作確認します。

```powershell
sync --doctor
```

Python が無い場合は [python.org](https://www.python.org/downloads/) からインストールし、`Add python.exe to PATH` を有効にしてください。

自動 commit を使う場合、Git の作者名が必要です。未設定なら一度だけ:

```powershell
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

## sync の使い方

### 初回

どこでも次を実行します。

```powershell
sync
```

`D:\dev` 配下の Git リポジトリを検出して一覧表示します。番号で同期対象を選びます。

```
例: 1,2,5
例: 3-6
例: all
例: q   （何も選ばない）
```

選んだ内容は `repositories.json` に保存されます。以後はここに載っている **enabled=true** のプロジェクトを同期します。  
`D:\dev` に後から増えたソースプロジェクトは、日常の `sync`（毎日のタスク含む）で自動的に対象へ入ります。`--disable` したものは再有効化しません。

### 2 回目以降

```powershell
sync
```

`D:\dev` に新しいソースプロジェクトが増えていれば、先に同期対象へ追加します。

- Git があり remote もある → 有効な対象として追加（`[ADD]`）
- Git はあるが remote が無い → プライベート GitHub リポジトリを作って `origin` を付け、対象に追加（`[CREATE]`）
- Git が無いソースフォルダ → `git init` のうえ同様に GitHub 作成
- venv / logs / `ffmpeg` だけの置き場 / exe だけのフォルダは無視
- `sync --disable` したものは触らない

そのあと、有効な各プロジェクトについて次をこの順で行います。

1. Git リポジトリとして正常か確認
2. remote を確認
3. 現在の branch を確認
4. `git fetch` でリモートの最新を取得
5. ローカル変更の有無を確認
6. リモート変更の有無を確認
7. 双方の差分を確認
8. 安全なら同期
9. ローカル変更があれば commit（変更が無いのに commit はしません）
10. GitHub へ push
11. 最終状態を確認

自動 commit メッセージ:

```
auto sync YYYY-MM-DD HH:MM:SS
```

### Cursor からワンクリック

1. このフォルダを Cursor で開く
2. `Terminal` → `Run Task…` → **Git Sync All**

### 判定結果

| 表示 | 意味 |
|---|---|
| `[OK]` | 同期完了 |
| `[PULL]` | リモート変更を fast-forward で取得 |
| `[PUSH]` | ローカル変更を commit / push |
| `[ADD]` | `D:\dev` の新規プロジェクトを同期対象に追加 |
| `[CREATE]` | GitHub リポジトリを新規作成して対象に追加 |
| `[CONFLICT]` | 競合、または双方に進んだ変更があり停止 |
| `[ERROR]` | 接続エラーなど |

終了後に集計を出します。

```
同期成功：8件
変更なし：12件
コンフリクト：1件
接続エラー：0件
その他エラー：1件
```

あるプロジェクトが失敗しても、他の安全なプロジェクトは続行します。

### 安全に自動同期しないケース

次の場合、そのプロジェクトだけ停止します。上書きしません。

- ローカルとリモートの双方に新しい commit がある
- ローカルに未コミット変更があり、リモートにも新しい commit がある
- merge / rebase / cherry-pick の途中
- 未解決のコンフリクトがある
- detached HEAD
- remote が無い、または同名 branch がリモートに無い

同期できるのは次だけです。

- 完全に同一 → 何もしない
- リモートだけ進んでいる、作業ツリーはきれい → `merge --ff-only`
- ローカルだけ進んでいる（未コミット含む） → 必要なら commit して push

## 同期対象プロジェクトの追加方法

新しいソースフォルダを `D:\dev` に置くだけで、次の `sync` が自動で対象に入れます。手動でも追加できます。

初回の選択をやり直す:

```powershell
sync --init
```

1 件追加:

```powershell
sync --add D:\dev\project-d
```

または `repositories.json` に追記して `enabled` を `true` にします。

```json
{
  "name": "project-d",
  "path": "D:\\dev\\project-d",
  "enabled": true
}
```

無効化していたものを再び有効化:

```powershell
sync --enable project-d
```

## GitHub リポジトリが無いプロジェクト

日常の `sync` は、ソースらしい新規フォルダなら GitHub リポジトリも自動作成します。  
確認しながら作りたいときだけ:

```powershell
sync --provision
```

確認画面のあと、番号で作成するプロジェクトを選びます。

- Git はあるが remote が無いもの
- `D:\dev` 直下で Git 未初期化の開発フォルダ（venv / logs は除外）

すでに remote があるプロジェクトは変更しません。

確認を省略する場合:

```powershell
sync --provision --yes
```

公開リポジトリにしたい場合は `config.json` の `new_repo_private` を `false` にします。

## 同期対象から外す方法

登録ごと削除:

```powershell
sync --remove project-d
```

残したまま一時的に外す:

```powershell
sync --disable project-d
```

`repositories.json` の `enabled` を `false` にしても同じです。

プロジェクト本体の Git リポジトリは削除しません。

## コンフリクト時の対応

該当プロジェクトだけ `[CONFLICT]` になり、次を表示します。

- プロジェクト名
- パス
- 現在の branch
- 競合ファイル（分かる場合）
- Git の状態

対応例:

```powershell
cd D:\dev\project-e
git status
```

Cursor でそのプロジェクトを開き、競合を手動で解消して commit してください。  
解消後にもう一度 `sync` すれば、安全な状態ならそのプロジェクトも同期されます。

このツールはコンフリクトを自動解消しません。

## ログの確認方法

ログは管理ディレクトリの `logs` に保存されます。

```
D:\dev\cloud-agent-sync\logs\sync-YYYYMMDD-HHMMSS.log
```

clone 先が違う場合は、その clone 先の `logs` です。

ログには次を記録します。

- 実行日時
- 対象プロジェクト
- 実行した Git 処理
- 成功 / 失敗
- エラー内容

最新ログの場所は、`sync` 終了時にも表示されます。

## その他のコマンド

日常は不要です。設定を変えるときだけ使います。

```powershell
sync --scan         # 検出するだけ（設定は変えない）
sync --list         # 登録済み一覧
sync --provision    # GitHub が無いプロジェクトを新規作成して対象に入れる
sync --dry-run      # 判定だけ。commit / merge / push しない
sync --doctor       # Git / Python / D:\dev / PATH / 通知メールを点検
sync --notify-test  # 通知メールの送信テスト（同期はしない）
```

設定ファイル:

| ファイル | 用途 |
|---|---|
| `config.json` | 同期ルート（既定 `D:\dev`）、深さ、自動 commit、gitignore、新規プロジェクト自動追加、通知メール宛先など |
| `notify.local.json` | SMTP アプリパスワード。Git 管理しない。`notify.local.example.json` をコピーして作る |
| `gitignore.template` | 各プロジェクトへ入れる「ソース以外を除外」のひな形 |
| `repositories.json` | 同期対象。初回 `sync` で生成。Git 管理しない（マシン固有） |
| `repositories.example.json` | 見本 |

`ensure_gitignore` が true のとき、`sync` と `sync --provision` は各プロジェクトの `.gitignore` に管理用ブロックを足します。既存の独自ルールは残します。ログ、venv、`ffmpeg/`、`.env` などは GitHub に送りません。すでに commit 済みの大きなファイルは履歴から自動削除しません。

新規プロジェクトの自動追加を止める場合は `config.json` で次を `false` にします。

| キー | 意味 |
|---|---|
| `auto_add_projects` | `D:\dev` の未登録フォルダを `sync` で取り込む |
| `auto_provision_github` | remote が無い／Git 未初期化のソースフォルダを GitHub に作成する |
| `auto_init_non_git` | Git が無いソースフォルダを `git init` してから作成する |

## Windows タスク登録

このプログラムは AI を使いません。中身は Python と Git（必要なら GitHub CLI）だけです。タスク実行時も AI は呼ばれません。

毎週日曜 23:00 に `sync` だけ走らせるバッチです。一度だけ実行します。

```powershell
cd D:\dev\cloud-agent-sync
git pull
.\register-task.cmd
```

同じ名前の古いタスク（毎日 20:00 など）があれば上書きします。時刻だけ変える例:

```powershell
.\register-task.cmd 22:30
```

外すとき:

```powershell
.\register-task.cmd /unregister
```

タスクが実際に起動するファイルは `run-sync-task.cmd` です。`sync` だけ呼び、`--init` は使いません。

## 通知メール

毎週のタスクでも、手で `sync` したときでも、**開始時と終了時に必ずメール**します。コンフリクトやエラーでも終了メールは送ります。届かないと実行に気づけないためです。

宛先の既定は `modafang111@gmail.com`（`config.json` の `notify_email`）です。Gmail は通常のログインパスワードでは送れません。**アプリパスワード**が必要です。

```powershell
cd D:\dev\cloud-agent-sync
git pull
copy notify.local.example.json notify.local.json
notepad notify.local.json
```

1. [Google アカウント](https://myaccount.google.com/apppasswords) で 2 段階認証を有効にする
2. 「アプリパスワード」を発行する（アプリ名は `cloud-agent-sync` でよい）
3. `notify.local.json` の `smtp_password` に、発行された 16 文字を貼る
4. テストする

```powershell
sync --notify-test
```

件名の見方:

| 件名 | 意味 |
|---|---|
| `同期を開始しました` | タスク／`sync` が動き出した |
| `同期完了` | 問題なく終わった |
| `要確認` | コンフリクトまたはエラーあり（本文に詳細） |
| `同期失敗` | 途中で停止、または Python が無い |

`notify.local.json` は GitHub に上げません。パスワードを `config.json` に書かないでください。

注意:

- 事前に一度 `sync` または `sync --init` 済みであること（初回の番号選択はタスクではできません）
- タスクが実行するのは `sync` だけです。AI は使いません
- `D:\dev` に増えたソースプロジェクトは、この `sync` が自動で対象に入れ、必要なら GitHub も作ります
- `--disable` したプロジェクトは自動では戻しません
- Windows にログイン中の方が、GitHub 認証が安定します
- コンフリクトのプロジェクトは、いつもどおりその件だけ止まります。結果は通知メールでも届きます


## Cursor Cloud Agent 向け

Cloud Agent はこのリポジトリのプログラムを編集できます。  
ただし Cloud Agent の実行環境から `D:\dev` を直接操作できるとは限りません。

各開発プロジェクトの変更は、そのプロジェクトの GitHub リポジトリ経由でローカル Cursor と同期してください。  
ローカルでは `sync` が、登録済みプロジェクトをまとめて GitHub と揃えます。

## 破壊的操作の禁止

次は実装上も禁止しています。

- `git push --force` および `--force-with-lease`
- `git reset --hard`
- `git clean -fd`
- ファイル削除コマンド
- rebase や commit --amend による履歴の強制書き換え
- remote / upstream / 現在 branch の変更（`sync --provision` で remote が無い場合に `origin` を追加する処理を除く）
