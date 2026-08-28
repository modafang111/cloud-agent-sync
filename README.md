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

日常操作は **`sync` だけ** です。常駐監視・定期実行・起動時自動同期はありません。

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

選んだ内容は `repositories.json` に保存されます。以後はここに載っている **enabled=true** のプロジェクトだけを同期します。

### 2 回目以降

```powershell
sync
```

各プロジェクトについて、次をこの順で行います。

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
| `[SKIP]` | 変更なし |
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

日常の `sync` は、**すでに Git があり、すでに remote があるプロジェクト** だけを同期します。  
GitHub リポジトリが無いもの、Git 自体が無いフォルダは、次のコマンドで作成して対象に入れます。

```powershell
sync --provision
```

確認画面のあと、次を行います。

1. `D:\dev` 直下で Git 未初期化のフォルダを `git init`
2. Git はあるが remote が無いプロジェクトを検出
3. GitHub に同名リポジトリが無ければ **新規作成**（既定は private）
4. 既存の中身がある GitHub リポジトリには接続しない（履歴衝突を避ける）
5. `git remote add origin` して push
6. `repositories.json` の同期対象に追加

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
sync --scan      # 検出するだけ（設定は変えない）
sync --list      # 登録済み一覧
sync --provision # GitHub が無いプロジェクトを新規作成して対象に入れる
sync --dry-run   # 判定だけ。commit / merge / push しない
sync --doctor    # Git / Python / D:\dev / PATH を点検
```

設定ファイル:

| ファイル | 用途 |
|---|---|
| `config.json` | 同期ルート（既定 `D:\dev`）、深さ、自動 commit など |
| `repositories.json` | 同期対象。初回 `sync` で生成。Git 管理しない（マシン固有） |
| `repositories.example.json` | 見本 |

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
